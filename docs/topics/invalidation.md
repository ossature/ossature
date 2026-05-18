# How Invalidation Works

When the build loop encounters a `done` task, it does not simply skip it. It checks whether the task is still valid by comparing two hashes stored in `.ossature/state.toml` against the current state of things on disk. This mechanism exists so that edits to specs, context files, or upstream outputs propagate to exactly the tasks that need to rebuild, without touching tasks whose inputs have not changed.

## Input hash

The input hash is a SHA-256 over the assembled prompt (project brief, spec brief, task description, all referenced spec and architecture sections, cross-spec interface content) plus the raw bytes of any `context_files`.

`inject_files` are intentionally excluded from the input hash. Instead, the build loop tracks which tasks were rebuilt during the current run. If any task in a `done` task's `depends_on` list was rebuilt this run, the done task rebuilds too, regardless of hash. This avoids false invalidation when a later task edits an injected file.

If you reword a spec section, add a context file, or change a cross-spec interface, the input hash will not match. That task gets rebuilt.

## Output hash and file ownership

The output hash is a SHA-256 over the files the task created. Not all files it touched, only the ones it owns.

Ossature tracks two separate lists per task: `created_files` and `edited_files`. When a task uses `write_file` or `copy_context_file`, the file goes into `created_files`. When a task uses `edit_file` on a file that some other task created, it goes into `edited_files`. If a task edits a file it created itself, that file stays in `created_files`.

Only `created_files` are hashed for the output check. `edited_files` are recorded in `state.toml` for traceability but do not participate in invalidation.

The reason for this distinction is the multi-task editing pattern. Task 001 might create `src/lib.rs` as a scaffold, and task 010 might later edit that file to add the real implementation. Without ownership tracking, the output hash for task 001 would be computed against whatever `src/lib.rs` looks like on disk right now, including task 010's changes. The hash would not match, task 001 would be flagged as stale, it would rebuild, and that would invalidate everything downstream, causing a cascade rebuild for no good reason.

With ownership tracking, task 001's hash only covers what task 001 created. Task 010's edit is task 010's responsibility, recorded in task 010's `edited_files`, and task 001 does not care about it.

## The staleness check

For each `done` task, the build loop does this:

1. Assemble the prompt from current data.
2. Compute the input hash and compare it to what is stored. If different, rebuild.
3. Compute the output hash over the task's `created_files` and compare. If different, rebuild.
4. If both match, the task is still valid and gets skipped.

When a task is stale, it gets rebuilt in the same loop iteration. It is not deferred. This way downstream tasks always see their dependencies as `done` when they are reached.

## Cascading

Invalidation cascades through the dependency graph automatically. If you edit `auth.smd` and run `ossature build`:

1. AUTH tasks that reference the changed sections have a different input hash, so they rebuild.
2. Their outputs change, which means downstream AUTH tasks that inject those files see different content in their input hash. They rebuild too.
3. Once all AUTH tasks finish, the AUTH interface gets re-extracted.
4. If the interface changed, tasks in other specs that reference AUTH's interface now have a different input hash. They rebuild.
5. If the interface did not change, those cross-spec tasks are untouched.

This is the same idea as header files in C. Change the `.c` without changing the `.h` and nothing downstream recompiles.

## Backfill

If a task is `done` in the plan but has no entry in `state.toml`, perhaps because you deleted the state file or edited the plan by hand, Ossature trusts the status. It reads the task's `output.toml` to find which files it created, computes both hashes from current data, and stores them. No rebuild happens.

## Force-quit safety

Because output hashes only cover a task's own created files, they are correct the moment they are written. There is no end-of-build fixup pass. If the build is interrupted by Ctrl+C or a crash, the state on disk is already consistent. The next build picks up where it left off.

For the schema of the state file, see [state.toml reference](../reference/state-toml.md).
