# The Build System

This covers how the build loop, fix loop, invalidation, and retry work together.

## The .ossature/ Directory

All Ossature state lives in `.ossature/`. Here's what's inside after an audit and build:

```
.ossature/
├── manifest.toml              # Checksums of all input files
├── graph.toml                 # Resolved spec dependency graph
├── audit-report.md            # Audit findings across all specs
├── plan.toml                  # The build plan (editable)
├── state.toml                 # Per-task input/output hashes
├── audits/
│   └── EXPENSE_TRACKER.json   # Cached per-spec audit results
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
spec_refs = ["EXPENSE_TRACKER:Goals", "EXPENSE_TRACKER:Constraints"]
arch_refs = ["EXPENSE_TRACKER:Dependencies"]
status = "pending"
verify = "uv run python -c 'import spenny'"

[[task]]
id = "002"
spec = "EXPENSE_TRACKER"
title = "Storage Layer"
outputs = ["src/spenny/storage.py"]
depends_on = ["001"]
inject_files = ["pyproject.toml", "src/spenny/__init__.py"]
status = "pending"
verify = "uv run python -c 'from spenny.storage import load, save'"
```

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

## The Fix Loop

When verification fails:

1. Build a repair prompt with the error output, the current file contents, and a reference to the original task
2. Create a fresh fixer agent (separate from the original, no accumulated history)
3. The fixer reads the errors and uses the same tools to fix the code
4. Run verification again
5. If it fails, repeat
6. After `max_fix_attempts` failures (default 3), mark the task as failed

Each fix attempt's prompt and response get saved to the task directory for debugging (`fix-1-prompt.md`, `fix-1-response.md`, etc.).

## Build Modes

**Default** - continues silently on success. On failure (after fix attempts), pauses with a prompt: retry, skip, or quit.

**Step** (`--step`) - pauses after every successful task. Lets you inspect the output before continuing.

**Auto** (`--auto`) - runs without pausing. Stops on the first failure.

**Auto-skip** (`--auto --skip-failures`) - runs without pausing. Marks failures and continues with the next task. Reports all failures at the end. Skips tasks whose dependencies weren't met.

## How Invalidation Works

When `ossature build` encounters a task marked as `done`, it doesn't just skip it. It verifies it's still valid:

1. Assemble the prompt using current data
2. Compute the input hash (SHA-256 of the prompt text, inject_files contents, and context file contents)
3. Compare to the stored hash in `state.toml`

If the hashes match, check the output files on disk against the stored output hash. If those also match, the task is still valid and gets skipped.

If the input hash doesn't match (spec changed, upstream output changed, interface file changed), the task gets marked as `pending` and rebuilt.

If the output hash doesn't match (someone manually edited the generated files), the task also gets re-run.

This cascades automatically. If you edit `auth.smd` and run `ossature build`:

1. AUTH tasks that reference the changed sections get invalidated
2. Their outputs change, which invalidates downstream AUTH tasks that inject those files
3. After all AUTH tasks complete, the AUTH interface gets re-extracted
4. If the interface changed, cross-spec tasks in API that reference AUTH's interface get invalidated
5. If the interface didn't change, API tasks are untouched

## How Retry Works

`ossature retry` manipulates task statuses in the plan and delegates to the build loop.

**`ossature retry`** (no flags) - sets all `failed` tasks to `pending`, then builds.

**`ossature retry --from 007`** - sets every task with ID >= 007 to `pending`, regardless of current status. Effectively "redo everything from this point."

**`ossature retry --only 005`** - sets task 005 to `pending`, finds all tasks that transitively depend on it using a breadth-first search through the dependency graph, and sets those to `pending` too. Then builds.

After retry resets statuses, the build loop handles everything: verifying hashes on `done` tasks, rebuilding `pending` tasks.

## Incremental Re-Planning

When you change only some specs and re-run `ossature audit`, it performs an incremental re-plan instead of regenerating everything:

- Only the changed specs get sent to the LLM for new task planning
- Tasks for unchanged specs are preserved with their existing IDs, hashes, and statuses
- Task directories and build state (`state.toml`) are remapped to match the new plan numbering
- Output files from old tasks that no longer appear in the new plan are automatically deleted

This means a change to one spec in a multi-spec project won't discard progress on unrelated specs. The project brief is also preserved during incremental audits to avoid invalidating input hashes for all tasks.

Use `--replan` to force a full plan regeneration from scratch.

## LLM Error Handling

All LLM errors during audit or build are caught and displayed in a formatted panel instead of raw tracebacks. Specific errors include:

- **Rate limits (429)** - retried with exponential backoff, starting at 30 seconds, up to 5 retries
- **Insufficient credits (402)** - reported with a suggestion to check your account
- **Server errors (500+)** - reported with a suggestion to wait and retry
- **Usage limit exceeded** - reported when a task exceeds the maximum number of LLM requests
- **Other agent errors** - caught and displayed with the error message and a suggestion to retry
