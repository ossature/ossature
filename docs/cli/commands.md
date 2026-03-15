# Commands

## ossature init

Create a new project.

```bash
ossature init myproject
```

Creates a directory with `ossature.toml` and a `specs/` folder. Use `.` to initialize the current directory.

## ossature new

Create a new spec file from a template.

```bash
ossature new my-feature              # creates specs/my-feature.smd
ossature new my-feature -t amd       # creates specs/my-feature.amd
ossature new my-feature -i           # interactive mode
```

## ossature validate

Parse all `.smd` and `.amd` files and check for structural issues. No LLM calls.

```bash
ossature validate
```

Checks that every `@depends` target exists, every `@spec` reference in AMDs resolves to a real SMD, there are no duplicate component names within a spec, and there are no cycles in the dependency graph.

## ossature audit

The most complex command. Sends specs to the LLM for review, generates context files, and produces a build plan.

```bash
ossature audit
```

What it does, in order:

1. Validates everything (same checks as `validate`)
2. Builds the spec dependency graph, writes `.ossature/graph.toml`
3. Computes checksums of all source files, compares to saved manifest. If nothing changed, asks whether to re-audit
4. Audits each changed spec with the LLM, looking for ambiguity, contradictions, gaps, and feasibility issues
5. Runs a cross-spec audit if there are multiple specs, checking for interface mismatches
6. Writes the audit report to `.ossature/audit-report.md`
7. Generates a project brief and per-spec briefs
8. Extracts or infers interface signatures for each spec
9. Generates the build plan, writes `.ossature/plan.toml`

When only some specs changed, audit runs **incrementally**: only the changed specs are re-planned, while tasks for unchanged specs are preserved with their existing hashes. Stale output files from tasks that no longer exist in the new plan are automatically removed. The project brief is also skipped during incremental audits to avoid invalidating input hashes for all preserved tasks.

Use `--replan` to force a full plan regeneration, discarding manual edits and any incremental state:

```bash
ossature audit --replan
```

## ossature build

Execute the build plan, generating code task by task.

```bash
ossature build
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--step` | Pause after every successful task for approval |
| `--auto` | Run to completion, stop on failure |
| `--auto --skip-failures` | Run everything possible, skip failures, report at end |
| `--spec AUTH` | Only build tasks for the named spec and its dependencies |
| `--force` | Reset all tasks to pending, full rebuild |

`--step` and `--auto` are mutually exclusive. `--skip-failures` requires `--auto`.

In the default mode (no flags), the build continues silently on success and pauses on failure with a prompt: retry, skip, or quit.

## ossature retry

Re-run failed or specific tasks.

```bash
ossature retry                # re-run all failed tasks
ossature retry --from 007     # redo everything from task 007 onwards
ossature retry --only 005     # re-run task 005 and all its dependents
```

`--from` and `--only` are mutually exclusive.

With `--only`, Ossature walks the dependency graph to find every task that transitively depends on the specified task and marks those as pending too.

## ossature status

Show current build progress.

```bash
ossature status
```

Shows how many tasks are done, pending, failed, or skipped.

## ossature clean

Remove the `.ossature/` directory. Full reset.

```bash
ossature clean
```

## Global Options

| Option | Description |
|--------|-------------|
| `--config`, `-c` | Path to ossature.toml |
| `--verbose`, `-v` | Verbose output |
| `--version` | Show version |
