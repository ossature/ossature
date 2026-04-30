# Changelog

All notable changes to Ossature are documented here.

This project follows [Semantic Versioning](https://semver.org/).

## 0.0.4 - 2026-04-30

Incremental re-planning is a lot less destructive now. Before this release, editing one spec in a multi-spec project preserved tasks for the *other* specs but regenerated everything for the spec you touched, so a one-line wording fix could throw away build progress on the rest of that spec's tasks. The planner now sees a unified diff of what changed in the spec plus the previous task plan, and its default mode is to preserve. It emits a `PreservedTaskRef` for every previous task the diff doesn't affect and only writes a full task when something is genuinely new or modified. On top of that, when the new plan contains a task whose outputs exactly match a task from the previous plan, the old status (`done`, `manual`, `skipped`) and notes carry over, so a typo fix won't reset finished work to pending.

Project and spec briefs used to be regenerated on every full audit, and since LLM output is non-deterministic that quietly invalidated input hashes for every task on each run. Briefs are now content-addressed against the model and the prompt that produces them, and the spec brief input was narrowed to the spec's title, dependencies, and overview. Adding a requirement or an example no longer changes the brief input, so the brief is reused and task input hashes stay stable. Changing the overview or the project framework still regenerates the relevant brief, which is what you want.

Audit and planner prompts are now persisted on disk so you can see exactly what was sent to the model. Per-spec audits live under `.ossature/audits/<SPEC>/` with `prompt.md` and `response.json` next to each other, the cross-spec audit lives under `.ossature/audits/cross-spec/`, and the per-spec planner input and raw plan output live under `.ossature/planners/<SPEC>/`. The audit cache moved from `.ossature/audits/<SPEC>.json` to `.ossature/audits/<SPEC>/response.json` as part of this, so existing `.ossature` directories from 0.0.3 will need a re-audit.

### Added

- `.ossature/snapshots/<SPEC>.md` caches the rendered spec content used as planner input, so the next audit can diff it against the new content.
- `.ossature/planners/<SPEC>/{prompt.md, response.json}` records the exact planner input and the raw task plan returned by the model.
- `prompt.md` files alongside the cached audit responses under `.ossature/audits/<SPEC>/` and `.ossature/audits/cross-spec/`.
- `PreservedTaskRef` variant in the planner's output schema. The model references an unchanged previous task by its 1-based index instead of re-emitting the full task.
- `brief_inputs` and `project_brief_input` hash fields on the manifest, used to gate brief regeneration.

### Changed

- Incremental re-plan sends the spec diff and the previous task plan to the planner. The planner system prompt defaults to preservation and only emits a full task for new or modified work.
- Tasks in a changed spec carry over their status, notes, and id mapping when their output file set matches a task from the previous plan exactly.
- Spec briefs are generated from the spec's title, dependencies, and overview only, not the full rendered SMD, so requirement-level edits don't invalidate the brief.
- Audit data layout changed from a single `<SPEC>.json` per spec to a directory per spec containing `prompt.md` and `response.json`. Cross-spec audit data moved from `cross-spec.json` to `cross-spec/response.json`.
- `spec_refs` and `arch_refs` in `plan.toml` no longer carry the spec id prefix. `EXPENSE_TRACKER:Goals` is now just `Goals`, since the spec id is already on the task's `spec` field.

### Fixed

- Diff-aware re-planning was still losing progress inside the changed spec because the model would regenerate tasks from scratch even when the diff didn't touch them. The new `PreservedTaskRef` schema plus output-based status carry-over close that gap.
- Project and spec briefs were regenerated on every full audit, which invalidated input hashes for every task because LLM-generated text is non-deterministic. Briefs are now reused when their hashed inputs haven't changed.

## 0.0.3 - 2026-04-15

Build and audit now track LLM token usage and cost per task, printed as a summary when the run finishes. This makes it a lot easier to understand where your budget is going, especially on larger specs with many tasks.

Validate catches circular dependencies in the spec graph now. It was silently accepting cycles before, which would cause confusing failures later in audit or build. Also added a complexity warning for requirements that are doing too much, so you get a heads-up during validate instead of finding out when the LLM generates a mess.

Malformed API responses (bad JSON from the provider) are retried instead of crashing the whole run. The `.ossature` metadata directory is no longer ignored by default in the generated `.gitignore`.

### Changed

- Token usage and estimated cost are tracked and displayed per task during build and audit.
- Validate now runs DFS-based cycle detection on the spec dependency graph.
- Validate warns when individual requirements look overly complex.
- Audit's internal validation was refactored to share code with the validate command instead of duplicating checks.

### Fixed

- Circular dependencies in spec graphs were silently accepted by validate.
- Malformed JSON responses from LLM providers caused unhandled `JSONDecodeError` crashes.
- `.ossature` metadata directory was excluded by the default generated `.gitignore`, which meant audit/build state wasn't committable.
- Operator precedence bug in `is_verify_command_error` could misclassify source-code errors as command invocation failures.

## 0.0.2 - 2026-03-29

Audit is now non-interactive by default. It was pretty annoying having to answer 3 prompts per spec per fix cycle, so now it just runs through everything silently, fixes errors, and prints a summary at the end. You can still get the old behavior with `--interactive` if you want it. Also added `--no-fix` and `--errors-ok` flags.

### Changed

- Audit findings now reference correct line numbers. Previously we were re-rendering specs to markdown before sending them to the LLM which meant the line numbers were always off. Now the raw file content is sent with line prefixes so the LLM sees real source lines.
- The `edit_file` tool parameter is now a typed `list[dict]` instead of raw JSON string. Less capable models were consistently failing at the JSON-within-JSON encoding so this should help.
- LLM agent failures now include context about what was happening (operation name, spec id, model) instead of dumping raw pydantic-ai errors.
- Build retries with fresh context when `edit_file` hits structural schema errors, and re-snapshots output hashes for completed tasks on new builds.

### Added

- `tool_retries` config option (default 5), separate from `retries` (default 3). Tool-using agents need more headroom.
- `[audit]` config section with configurable `max_fix_cycles`.

### Fixed

- Audit line numbers pointing to wrong locations in source files.
- Retry logic on structural edit_file schema errors during builds.

## 0.0.1 - 2026-03-18

First public release.

### Added

- Project initialization with `ossature init`
- SMD spec format with metadata fields, requirements, examples, and constraints
- AMD architecture format with components, interfaces, data models, and flows
- Structural validation (`ossature validate`) with dependency graph checks
- LLM-powered audit (`ossature audit`) with findings, auto-fix, and build plan generation
- Incremental builds (`ossature build`) with per-task verification and fix loops
- Retry mechanism for failed tasks (`ossature retry`)
- Support for Anthropic, OpenAI, Mistral, Google, and Ollama
- MkDocs documentation site
