# How Invalidation Works

When `ossature build` encounters a task marked as `done`, it doesn't just skip it. It checks whether the task is still valid by comparing two hashes in `.ossature/state.toml` against the current state of things on disk.

## Input hash

The input hash is a SHA-256 over everything the task saw when it ran. That means the full assembled prompt (project brief, spec brief, task description, all referenced spec and arch sections, cross-spec interface content), plus the contents of any `inject_files` and `context_files`.

If you reword a spec section, or an upstream task produces different output, or an interface file gets re-extracted with different signatures, the input hash won't match anymore. The task gets rebuilt.

## Output hash and file ownership

The output hash is a SHA-256 over the files the task created. Not all files it touched, just the ones it owns.

Ossature tracks two separate lists per task: `created_files` and `edited_files`. When a task uses `write_file` or `copy_context_file`, the file goes into `created_files`. When a task uses `edit_file` on a file that some other task created, it goes into `edited_files`. If a task edits a file it created itself, nothing changes, it's already in `created_files`.

Only `created_files` are hashed for the output check. `edited_files` are recorded in `state.toml` for traceability but they don't participate in invalidation at all.

Why does this matter? Think about a pretty common situation: task 001 creates `src/lib.rs` as a scaffold, then task 010 comes along and edits that file to add the real implementation. Without ownership tracking, the output hash for task 001 would be computed against what `src/lib.rs` looks like on disk right now, which includes task 010's changes. Next time you build, the hash won't match, task 001 gets flagged as stale, it rebuilds, that invalidates everything downstream, and you end up rebuilding half the project for no reason.

With ownership tracking, task 001's hash only covers what task 001 created. Task 010's edit to that file is task 010's business, recorded in task 010's `edited_files`, and task 001 doesn't care about it.

## The staleness check

For each `done` task, the build loop does this:

1. Assemble the prompt from current data
2. Compute the input hash and compare it to what's stored. If different: "input changed", rebuild.
3. Compute the output hash over the task's `created_files` and compare. If different: "output modified", rebuild.
4. Both match: task is still valid, skip it.

When a task is stale, it gets rebuilt right there in the same loop iteration. It doesn't get deferred. This way downstream tasks always see their dependencies as `done` when they're reached.

## Cascading

Invalidation cascades through the dependency graph on its own. Say you edit `auth.smd` and run `ossature build`:

1. AUTH tasks that reference the changed sections have a different input hash, so they rebuild.
2. Their outputs change, which means downstream AUTH tasks that inject those files see different content in their input hash. They rebuild too.
3. Once all AUTH tasks finish, the AUTH interface gets re-extracted.
4. If the interface changed, tasks in other specs (like API) that reference AUTH's interface now have a different input hash. They rebuild.
5. If the interface didn't change, those cross-spec tasks are untouched.

Same idea as header files in C. Change the `.c` without changing the `.h` and nothing downstream recompiles.

## Backfill

If a task is `done` in the plan but has no entry in `state.toml` (maybe you deleted the state file, or edited the plan by hand), Ossature trusts the status. It reads the task's `output.toml` to figure out which files it created, computes both hashes from current data, and stores them. No rebuild.

## Force-quit safety

Because output hashes only cover a task's own created files, they're correct the moment they're written. There's no end-of-build fixup pass needed. If the build gets interrupted, Ctrl+C, crash, whatever, the state on disk is already consistent. Next `ossature build` picks up where it left off.
