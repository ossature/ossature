# How the Build Works

This covers how the build loop, fix loop, and retry work together.

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
5. If it fails, repeat. If the fixer makes no file changes, it gets one retry with a nudge before counting it as a failed attempt
6. After `max_fix_attempts` failures (default 3), mark the task as failed

Each fix attempt's prompt and response get saved to the task directory for debugging (`fix-1-prompt.md`, `fix-1-response.md`, etc.).

## Build Modes

**Default** - continues silently on success. On failure (after fix attempts), pauses with a prompt: retry, skip, or quit.

**Step** (`--step`) - pauses after every successful task. Lets you inspect the output before continuing.

**Auto** (`--auto`) - runs without pausing. Stops on the first failure.

**Auto-skip** (`--auto --skip-failures`) - runs without pausing. Marks failures and continues with the next task. Reports all failures at the end. Skips tasks whose dependencies weren't met.

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
