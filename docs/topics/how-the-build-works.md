# How the Build Works

The build loop executes the plan produced by audit. Each task in `plan.toml` represents one unit of work: the LLM generates or edits files, a verify command runs, and if verification fails the fix loop kicks in. What follows is an explanation of each stage and why it works the way it does.

## Setup

If the `[build]` section in `ossature.toml` has a `setup` list, those commands run once before the first task. Setup only runs on fresh builds where no task is `done` and no prior state file exists. If you resume a partial build or retry failed tasks, setup does not run again.

## The build loop

For each task in the plan:

1. Assemble the prompt from the project brief, spec brief, task description, relevant spec and architecture sections, and interface files for cross-spec dependencies. Tasks with `inject_files` have those filenames listed in the prompt; the LLM reads their contents on demand using its `read_lines` and `grep_file` tools.
2. Send the prompt to the LLM with tools for writing files, reading files, and running commands.
3. The LLM generates code and writes files to the output directory.
4. Run the verification command.
5. If verification fails, enter the fix loop.
6. If the task succeeds, record input and output hashes in `state.toml`.

All file operations by the LLM are sandboxed to the output directory. Attempts to write outside it or use path traversal get rejected, and the LLM is instructed to try again. Commands the LLM runs via its `run_command` tool are also sandboxed: shell expansions (backticks, `$()`, `$VAR`) are blocked, and commands time out after 120 seconds.

## Pre-flight tool check

Before running any task, Ossature scans every `verify`, `setup`, and `test` command across the plan and checks that each tool the shell would look up on `PATH` is actually installed. The point is to fail fast when something like `cargo`, `make`, `gcc`, or `npm` is missing, rather than burning LLM tokens generating code that cannot be verified.

The check follows the POSIX rule: the shell only consults `PATH` when the command name contains no `/`. So `make` and `cargo` get checked. Anything with a slash, like `./myapp`, `target/release/foo`, or `node_modules/.bin/eslint`, is invoked by direct file path and is a project artifact rather than a tool, so it is left alone.

If any required tool is missing, the build prints the missing names and the verify lines that referenced them, then exits before the first LLM call.

## Per-task verify scoping

A task's verify command runs immediately after that task completes. Earlier tasks listed in `depends_on` have already run, but later tasks have not. So the verify can only exercise things that already exist at that point.

A common situation is a scaffolding task that only emits a build config before any source exists. If that task's verify tries to run a full build, it will fail because the source is produced by a later task. The scaffolding is correct; there is just nothing to compile yet. The planner is told about this constraint when it generates the plan, so it should produce sensible verify commands by default. The same constraint applies when editing `plan.toml` by hand.

See the [How-to guides](../how-to/index.md) for guidance on choosing verify commands when adjusting tasks manually.

## The fix loop

When verification fails, Ossature enters a repair cycle:

1. Build a repair prompt with the error output, the current file contents, and a reference to the original task. Files larger than `max_inline_lines` (default 200) are not inlined; the fixer uses its read and grep tools to inspect them instead.
2. Create a fresh fixer agent, separate from the original, with no accumulated history.
3. The fixer reads the errors and uses the same tools to repair the code.
4. Run verification again.
5. If it fails, repeat. If the fixer makes no file changes, it gets up to 2 retries with a nudge prompt before the attempt counts against the fix limit.
6. After `max_fix_attempts` failures (default 3), mark the task as failed.

Each fix attempt's prompt and response are saved to the task directory as `fix-N-prompt.md` and `fix-N-response.md`. These are the primary debugging artifact when a task fails.

## Build modes

The mode controls what happens between tasks and on failure.

**Default** mode continues silently on success. On failure, it pauses with a prompt: retry, skip, or quit.

**Step** (`--step`) pauses after every successful task. Useful for inspecting output before continuing.

**Auto** (`--auto`) runs without pausing. Stops on the first failure.

**Auto-skip** (`--auto --skip-failures`) runs without pausing. Marks failures and continues with the next task, then reports all failures at the end.

**Force** (`--force`) resets every task's status to pending before building, regardless of whether it was previously done. Use this when you want a full rebuild from scratch without deleting state files.

## Task statuses

Tasks in `plan.toml` have a `status` field. The build loop reads and updates these as it runs.

`pending` tasks run normally. `done` tasks are validated by hash check and skipped if still valid. `failed` tasks caused the build to stop and can be retried. `skipped` tasks are bypassed silently.

`manual` is a special status you can set by hand in `plan.toml`. Tasks with `status = "manual"` are displayed with a notice and skipped, giving you a marker for tasks you intend to handle outside the build loop (integration steps, deployments, manual reviews). The build treats them as if they were done for dependency purposes.

## How retry works

`ossature retry` manipulates task statuses in the plan and then delegates to the build loop.

`ossature retry` with no flags sets all `failed` tasks to `pending`, then builds.

`ossature retry --from 007` sets every task with ID >= 007 to `pending`, regardless of current status. This effectively redoes everything from that point forward.

`ossature retry --only 005` sets task 005 to `pending`, finds all tasks that transitively depend on it using a breadth-first traversal of the dependency graph, and sets those to `pending` too. Then it builds.

After retry resets statuses, the build loop takes over and handles everything: verifying hashes on `done` tasks, rebuilding `pending` tasks.

## Incremental re-planning

When you change only some specs and re-run `ossature audit`, it performs an incremental re-plan rather than regenerating everything from scratch:

- Only the changed specs get sent to the LLM for new task planning.
- The planner sees a unified diff of what changed in the spec and the previous task plan, so it can preserve unaffected tasks rather than generating from scratch.
- Tasks for unchanged specs are preserved with their existing IDs, hashes, and statuses.
- Tasks in the changed spec that produce the same output files as before carry over their existing status and build state. A minor spec edit will not lose progress on tasks whose outputs have not changed.
- Task directories and build state in `state.toml` are remapped to match the new plan numbering.
- Output files from old tasks that no longer appear in the new plan are automatically deleted.

The diff-aware planner and output-based matching work together. The planner is instructed to keep tasks stable when the diff does not affect them, and the matching step verifies this by checking exact output file sets. Tasks that do not match start fresh as pending.

This means a change to one spec in a multi-spec project does not discard progress on unrelated specs. Even within the changed spec, unaffected tasks keep their build progress.

### Brief preservation

Project and spec briefs are content-addressed against the LLM input that produces them. The project brief depends on the project name, version, language, framework, and each spec's title, dependencies, and overview. Each spec brief depends on its own spec's title, dependencies, and overview. The hash of those inputs is stored in `manifest.toml`, and a brief is regenerated only when the hash changes or the brief file is missing.

Briefs are part of every task's prompt and feed into its input hash. Adding a requirement, an example, or a constraint to a spec leaves the brief inputs unchanged, so the brief is reused verbatim and the input hash stays stable for tasks that did not otherwise need to change. Editing the overview or changing the project framework regenerates the relevant brief, which causes those tasks to rebuild.

Use `--replan` to force a full plan regeneration from scratch.

## LLM error handling

All LLM errors during audit or build are caught and displayed in a formatted panel rather than raw tracebacks. Specific cases:

- **Rate limits (429)** - retried with exponential backoff, starting at 30 seconds, up to 5 retries
- **Insufficient credits (402)** - reported with a suggestion to check your account
- **Server errors (500+)** - reported with a suggestion to wait and retry
- **Usage limit exceeded** - reported when a task exceeds the maximum number of LLM requests
- **Other agent errors** - caught and displayed with the error message and a suggestion to retry
