# The Build System

This covers how the build loop, fix loop, invalidation, and retry work together.

## The .ossature/ Directory

All Ossature state lives in `.ossature/`. Here's what's inside after an audit and build:

```
.ossature/
├── manifest.toml              # Source file checksums and brief input hashes
├── graph.toml                 # Resolved spec dependency graph
├── audit-report.md            # Audit findings across all specs
├── plan.toml                  # The build plan (editable)
├── state.toml                 # Per-task input/output hashes
├── audits/
│   ├── EXPENSE_TRACKER/
│   │   ├── prompt.md          # Exact prompt sent to the auditor
│   │   └── response.json      # Cached per-spec audit findings
│   └── cross-spec/
│       ├── prompt.md          # Exact prompt sent to the cross-spec auditor
│       └── response.json      # Cached cross-spec audit findings
├── planners/
│   └── EXPENSE_TRACKER/
│       ├── prompt.md          # Exact prompt sent to the planner
│       └── response.json      # Raw per-spec task plan from the LLM
├── snapshots/
│   └── EXPENSE_TRACKER.md     # Rendered spec content for diffing
├── context/
│   ├── project-brief.md       # Project summary for LLM context
│   ├── spec-briefs/
│   │   └── EXPENSE_TRACKER.md # Per-spec summary
│   └── interfaces/
│       └── EXPENSE_TRACKER.md # Public interface signatures
└── tasks/
    ├── 001-project-scaffold/
    │   ├── prompt.md           # Exact prompt sent to LLM
    │   ├── response.md         # LLM's raw response
    │   └── output.toml         # Files written, verification result
    ├── 002-storage-layer/
    │   └── ...
    └── ...
```

Every prompt and response is saved in per-task directories. If something goes wrong at task 14, you can read `tasks/014-*/prompt.md` and `response.md` to see exactly what the LLM was asked and what it produced.

## The Plan

`plan.toml` is the central artifact. It lists every task in order with dependencies, spec references, and status.

```toml
[meta]
generated_at = "2026-03-10T18:09:18Z"
total_tasks = 8
specs = ["EXPENSE_TRACKER"]

[[task]]
id = "001"
spec = "EXPENSE_TRACKER"
title = "Project Config & Package Scaffold"
description = "Create pyproject.toml with project metadata..."
outputs = ["pyproject.toml", "src/spenny/__init__.py"]
depends_on = []
spec_refs = ["Goals", "Constraints"]
arch_refs = ["Dependencies"]
status = "pending"
verify = ["uv run python -c 'import spenny'"]

[[task]]
id = "002"
spec = "EXPENSE_TRACKER"
title = "Storage Layer"
outputs = ["src/spenny/storage.py"]
depends_on = ["001"]
inject_files = ["pyproject.toml", "src/spenny/__init__.py"]
status = "pending"
verify = ["uv run python -c 'from spenny.storage import load, save'"]
```

`verify` is a list of shell commands. Each step runs in its own shell, in order, and the task fails on the first non-zero exit. Multi-step pipelines stay readable as a list rather than a long `&&`-chained string:

```toml
verify = [
    "make clean",
    "make CFLAGS='-std=c99 -Wall -Wextra -pedantic'",
    "./myapp --help > /tmp/help.txt",
    "grep -q -- '--help' /tmp/help.txt",
]
```

A bare string still loads for backwards compatibility, so `verify = "make"` is treated the same as `verify = ["make"]`.

The plan is human-readable and human-editable. After `ossature audit` generates it, you can reorder tasks, add notes, skip tasks, or insert manual steps before running `ossature build`.

Key fields on each task:

- `depends_on` - which tasks must complete first
- `spec_refs` - which spec sections to include in the prompt
- `arch_refs` - which architecture sections to include
- `inject_files` - output files from earlier tasks that this task needs to see
- `verify` - command to run after generation to check the output
- `context_files` - files from the context directory to include

## The Build Loop

For each task in the plan:

1. Assemble the prompt (project brief, spec brief, task description, relevant spec/arch sections, interface files for cross-spec dependencies, injected files from earlier tasks)
2. Send it to the LLM with tools for writing files, reading files, and running commands
3. The LLM generates code and writes files to the output directory
4. Run the verification command
5. If verification fails, enter the fix loop
6. If the task succeeds, record input/output hashes in `state.toml`

All file operations by the LLM are sandboxed to the output directory. Attempts to write outside it or use path traversal get rejected, and the LLM is told to try again.

## Pre-Flight Tool Check

Before the build runs any task, Ossature scans every `verify`, `setup`, and `test` command across the plan and checks that each tool the shell would look up on `PATH` is actually installed. The point is to fail fast when something like `cargo`, `make`, `gcc`, `npm`, or `zig` is missing, instead of burning LLM tokens generating code that can't be verified.

The rule for what counts as a tool we need on `PATH` is the POSIX one. The shell only consults `PATH` when the command name contains no `/`. So `make` and `cargo` get checked. Anything with a slash, like `./myapp`, `target/release/foo`, `zig-out/bin/x`, `node_modules/.bin/eslint`, or `/tmp/test_bin`, is invoked by direct file path. Those are project artifacts, not tools, so we leave them alone. This works the same way for any language or build system, with no compiler-specific logic to maintain.

If any required tool is missing, the build prints the missing names and the verify lines that referenced them, then exits before the first LLM call.

## Per-Task Verify Scoping

A task's `verify` runs immediately after that task completes. Earlier tasks listed in `depends_on` have already run, but later tasks have not. So the verify can only exercise things that already exist at that point. Files this task or its dependencies produced are fair game, but files a later task is going to write are not.

The most common trap is a scaffolding task that only emits a build config like a Makefile, `package.json`, `Cargo.toml`, `build.zig`, or `CMakeLists.txt` before any source exists. If you set that task's verify to `make` or `cargo build`, it'll fail because the source the build references is produced by a later task. The Makefile itself is fine, the build just has nothing to compile yet. For scaffold-only tasks, use lightweight checks. File existence (`test -f Makefile`) is usually enough, sometimes a parse or syntax check, or a dry-run of a target that doesn't depend on the source. Save the full build for the task that actually writes the source, and make sure that task lists the scaffold task in its `depends_on`.

The planner is told about this scoping rule when it generates the plan, and should produce sensible verify commands by default. When you edit `plan.toml` by hand, the same rule applies.

## The Fix Loop

When verification fails:

1. Build a repair prompt with the error output, the current file contents, and a reference to the original task. Files larger than `max_inline_lines` (default 200) are not inlined; the fixer uses its `read_lines` and `grep_file` tools to inspect them instead
2. Create a fresh fixer agent (separate from the original, no accumulated history)
3. The fixer reads the errors and uses the same tools to fix the code
4. Run verification again
5. If it fails, repeat. If the fixer makes no file changes, it gets up to two retries with a nudge, counted across the whole fix loop, before a no-op counts as a failed attempt
6. After `max_fix_attempts` failures (default 3), mark the task as failed

Each fix attempt's prompt and response get saved to the task directory for debugging (`fix-1-prompt.md`, `fix-1-response.md`, etc.).

## Build Modes

**Default** - continues silently on success. On failure (after fix attempts), pauses with a prompt: retry, skip, or quit.

**Step** (`--step`) - pauses after every successful task. Lets you inspect the output before continuing.

**Auto** (`--auto`) - runs without pausing. Stops on the first failure.

**Auto-skip** (`--auto --skip-failures`) - runs without pausing. Marks failures and continues with the next task. Reports all failures at the end. Skips tasks whose dependencies weren't met.

## How Invalidation Works

When `ossature build` encounters a task marked as `done`, it doesn't just skip it. It checks whether the task is still valid by comparing two hashes in `.ossature/state.toml` against the current state of things on disk.

### Input hash

The input hash is a SHA-256 over everything the task saw when it ran. That means the full assembled prompt (project brief, spec brief, task description, all referenced spec and arch sections, cross-spec interface content), plus the contents of any `context_files`. The input hash does not cover `inject_files`. A later task that edits a file an earlier task injected would otherwise invalidate it for no good reason, so dependency rebuilds are tracked separately by recording which task IDs rebuilt during the run.

If you reword a spec section the task references, change a context file it pulls in, or an interface file gets re-extracted with different signatures, the input hash won't match anymore. The task gets rebuilt.

### Output hash and file ownership

The output hash is a SHA-256 over the files the task created. Not all files it touched, just the ones it owns.

Ossature tracks two separate lists per task: `created_files` and `edited_files`. When a task uses `write_file` or `copy_context_file`, the file goes into `created_files`. When a task uses `edit_file` on a file that some other task created, it goes into `edited_files`. If a task edits a file it created itself, nothing changes, it's already in `created_files`.

Only `created_files` are hashed for the output check. `edited_files` are recorded in `state.toml` for traceability but they don't participate in invalidation at all.

Why does this matter? Think about a pretty common situation: task 001 creates `src/lib.rs` as a scaffold, then task 010 comes along and edits that file to add the real implementation. Without ownership tracking, the output hash for task 001 would be computed against what `src/lib.rs` looks like on disk right now, which includes task 010's changes. Next time you build, the hash won't match, task 001 gets flagged as stale, it rebuilds, that invalidates everything downstream, and you end up rebuilding half the project for no reason.

With ownership tracking, task 001's hash only covers what task 001 created. Task 010's edit to that file is task 010's business, recorded in task 010's `edited_files`, and task 001 doesn't care about it.

### The staleness check

For each `done` task, the build loop does this:

1. Assemble the prompt from current data
2. Compute the input hash and compare it to what's stored. If different: "input changed", rebuild.
3. Compute the output hash over the task's `created_files` and compare. If different: "output modified", rebuild.
4. Both match: task is still valid, skip it.

When a task is stale, it gets rebuilt right there in the same loop iteration. It doesn't get deferred. This way downstream tasks always see their dependencies as `done` when they're reached.

### What state.toml looks like

```toml
[tasks.001]
input_hash = "sha256:a1b2c3..."
output_hash = "sha256:d4e5f6..."
created_files = ["src/lib.rs", "src/main.rs"]
edited_files = ["Cargo.toml"]   # only present when non-empty
```

`created_files` determines what gets hashed. `edited_files` is just there so you can see what the task touched beyond its own files.

### Cascading

Invalidation cascades through the dependency graph on its own. Say you edit `auth.smd` and run `ossature build`:

1. AUTH tasks that reference the changed sections have a different input hash, so they rebuild.
2. Once those AUTH tasks rebuild, the build loop records their IDs in a set of rebuilt tasks. Any downstream AUTH task that lists one of them in `depends_on` re-runs for that reason alone. Injected file contents are not part of the input hash, so this cascade follows the set of rebuilt tasks, not a hash change.
3. Once all AUTH tasks finish, the AUTH interface gets re-extracted.
4. API's first task lists AUTH's last task in its `depends_on`, so in this same run it re-runs for the reason in step 2, whether or not the interface changed.

The interface hash earns its keep on a later run. When you build again and AUTH is already up to date, API's tasks fold the AUTH interface content into their own input hash, so they skip when that content is unchanged and rebuild when a signature changes. This is the header-file idea, but it holds across runs rather than within the run that rebuilds AUTH: change an AUTH source file without changing its extracted interface, and the next build leaves API alone.

### Backfill

If a task is `done` in the plan but has no entry in `state.toml` (maybe you deleted the state file, or edited the plan by hand), Ossature trusts the status. It reads the task's `output.toml` to figure out which files it created, computes both hashes from current data, and stores them. No rebuild.

### Force-quit safety

Because output hashes only cover a task's own created files, they're correct the moment they're written. There's no end-of-build fixup pass needed. If the build gets interrupted, Ctrl+C, crash, whatever, the state on disk is already consistent. Next `ossature build` picks up where it left off.

## How Retry Works

`ossature retry` manipulates task statuses in the plan and delegates to the build loop.

**`ossature retry`** (no flags) - sets all `failed` tasks to `pending`, then builds.

**`ossature retry --from 007`** - sets every task with ID >= 007 to `pending`, regardless of current status. Effectively "redo everything from this point."

**`ossature retry --only 005`** - sets task 005 to `pending`, finds all tasks that transitively depend on it using a breadth-first search through the dependency graph, and sets those to `pending` too. Then builds.

After retry resets statuses, the build loop handles everything: verifying hashes on `done` tasks, rebuilding `pending` tasks.

## Incremental Re-Planning

When you change only some specs and re-run `ossature audit`, it performs an incremental re-plan instead of regenerating everything:

- Only the changed specs get sent to the LLM for new task planning
- The planner sees a unified diff of what changed in the spec and the previous task plan, so it can preserve unaffected tasks rather than generating from scratch
- Tasks for unchanged specs are preserved with their existing IDs, hashes, and statuses
- Tasks in the changed spec that produce the same output files as before carry over their existing status and build state. A minor spec edit won't lose progress on tasks whose outputs haven't changed
- Task directories and build state (`state.toml`) are remapped to match the new plan numbering
- Output files from old tasks that no longer appear in the new plan are automatically deleted

The diff-aware planner and output-based matching work together: the planner is instructed to keep tasks stable when the diff doesn't affect them, and the matching step verifies this by checking exact output file sets. Tasks that don't match (new outputs, split tasks, renamed files) start fresh as pending.

This means a change to one spec in a multi-spec project won't discard progress on unrelated specs, and even within the changed spec, unaffected tasks keep their build progress.

### Brief preservation

Project and spec briefs are content-addressed against the LLM input that produces them. The project brief depends on the project name, version, language, framework, and each spec's title, dependencies, and overview. Each spec brief depends on its own spec's title, dependencies, and overview. The hash of those inputs is stored in `manifest.toml`, and a brief is regenerated only when the hash changes (or the brief file is missing).

This matters because briefs are part of every task's prompt and feed into its input hash. Adding a requirement, an example, or a constraint to a spec leaves the brief inputs unchanged, so the brief is reused verbatim and the input hash stays stable for tasks that didn't otherwise need to change. Editing the overview or changing the project framework will regenerate the relevant brief, which is the right behavior — the new wording should propagate to every task that uses it.

Use `--replan` to force a full plan regeneration from scratch.

## LLM Error Handling

All LLM errors during audit or build are caught and displayed in a formatted panel instead of raw tracebacks. Specific errors include:

- **Rate limits (429)** - retried with exponential backoff, starting at 30 seconds, up to 5 retries
- **Insufficient credits (402)** - reported with a suggestion to check your account
- **Server errors (500+)** - reported with a suggestion to wait and retry
- **Usage limit exceeded** - reported when a task exceeds the maximum number of LLM requests
- **Other agent errors** - caught and displayed with the error message and a suggestion to retry
