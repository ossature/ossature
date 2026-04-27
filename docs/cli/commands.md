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

If a spec has high requirement complexity, validate prints a warning. Validation still passes, but the spec may cause problems during plan generation. Consider splitting it into smaller specs linked with `@depends`.

## ossature audit

The most complex command. Sends specs to the LLM for review, generates context files, and produces a build plan.

```bash
ossature audit
```

What it does, in order:

1. Validates everything (same checks as `validate`)
2. Builds the spec dependency graph, writes `.ossature/graph.toml`
3. Computes checksums of all source files, compares to saved manifest. If nothing changed, skips re-audit
4. Audits each changed spec with the LLM, looking for ambiguity, contradictions, gaps, and feasibility issues
5. Auto-fixes errors (up to 3 cycles per spec), re-auditing after each fix
6. Runs a cross-spec audit if there are multiple specs, checking for interface mismatches
7. Writes the audit report to `.ossature/audit-report.md`
8. Regenerates the project brief and per-spec briefs whose inputs changed since the last audit
9. Extracts or infers interface signatures for each spec
10. Generates the build plan, writes `.ossature/plan.toml`
11. Exits with code 1 if any audit errors remain unresolved

By default, the audit runs **non-interactively**: it auto-fixes errors without prompting, prints a consolidated findings table at the end, and exits with code 1 if errors remain.

When only some specs changed, audit runs **incrementally**: only the changed specs are re-planned. The planner receives a diff of what changed and the previous task plan, so it can keep unaffected tasks stable. Tasks in the changed spec that produce the same output files as before carry over their build status. Tasks for unchanged specs are preserved with their existing hashes. Stale output files from tasks that no longer exist in the new plan are automatically removed.

**Flags:**

| Flag | Description |
|------|-------------|
| `--replan` | Force a full plan regeneration, discarding manual edits |
| `--interactive`, `-i` | Prompt before each auto-fix; offers to fix warnings too |
| `--no-fix` | Audit only, never attempt auto-fix |
| `--errors-ok` | Exit 0 even when audit errors remain |

`--interactive` and `--no-fix` are mutually exclusive.

```bash
ossature audit --replan         # force full plan regeneration
ossature audit -i               # interactive mode with prompts
ossature audit --no-fix         # just show findings, don't fix
ossature audit --errors-ok      # don't fail on remaining errors
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
