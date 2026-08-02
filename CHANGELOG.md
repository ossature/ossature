# Changelog

All notable changes to Ossature are documented here.

This project follows [Semantic Versioning](https://semver.org/).

## 0.2.1 - 2026-08-02

Two fixes, one for incremental re-planning and one for what the LLM gets to see.

### Fixed

- Incremental re-plan no longer carries a `done` status onto a task the planner re-emitted. The planner brings back unaffected tasks as preserved references and only re-emits a task in full when the spec edit touches it, but the merge treated any new task whose output files matched an old one as safe to keep `done`. A task the edit affected could keep its status that way, so the build skipped it and the output was never checked against the changed spec. Now only planner-preserved tasks carry their status over; a re-emitted task resets to pending even when its outputs match. `manual` still survives, since it records that you own the file rather than that a build produced it. A `done` verification task also resets when the task that builds the file it checks is redone, because the old verification ran against output that no longer stands.
- The project version from `ossature.toml` is no longer part of any LLM prompt (project brief, planner, or task prompts). The model would hardcode it into generated source, where it goes stale because a version bump alone rebuilds nothing. The bump also changed every task's input hash and the project brief input, forcing a full rebuild. Bumping `[project] version` now leaves the plan and build state alone.

## 0.2.0 - 2026-07-20

This release adds VMD, a third spec format for writing the tests yourself instead of letting the model write both the code and the tests that grade it. A `.vmd` file states expected behavior as concrete cases. A group is a table: one function, one example row per case, each row a set of JSON inputs and the expected result. A scenario is a named behavior written as given, when, then steps, and its `when` can be a function call or a command run against the built program. You write the expected values, the LLM never sees them, and the build turns the cases into a real test suite the generated code has to pass. VMD is optional, like AMD.

Audit reads the cases alongside the spec, flags any case that contradicts a requirement or an AMD contract, and appends a deterministic verification task per group and per file's scenarios, after that spec's implementation tasks. When the task runs, Ossature serializes the cases to a fixture under `checks/`, generates a test harness from a fixed template, and runs the suite as the task's verify command. No LLM output is in that path, and the implementer's prompt never includes the VMD, so the code can't be written to fit the tests. A failing suite goes into the same fix loop a verify failure does, with one difference: the fixer is pointed at the implementation files and the whole `checks/` directory rejects its writes.

`ossature validate` grew a coverage table off the same cases: which requirements have a case and what covers them, which declared `**Errors:**` have no matching `!Error` case, and which `@covers` targets don't resolve. Coverage is advisory by default; `require_coverage = true` under `[test]` makes validate fail on an uncovered requirement. A requirement heading can carry a `.no-verify` marker when it isn't testable with concrete values, and a plan task can declare `covers` so a golden-file or roundtrip test outside the VMD still closes coverage for its requirement.

Building a multi-spec project no longer rebuilds a dependent spec when the upstream interface it relies on hasn't changed. Before, a change anywhere in an upstream spec forced every dependent to rebuild even when the interface those dependents see was identical. The build now gates that on the interface file's input hash, and falls back to rebuilding the dependent when the upstream produced no extractable interface, so a stale file on disk can't be trusted by accident. The rest of the release is the move to pydantic-ai 2.0 and a cleanup pass over audit, plan merge, the build loop, and the parsers.

### Added

- VMD verification format. A `.vmd` file holds groups (a function signature line followed by one case per row) and scenarios (named given/when/then behaviors, where `when` is a function call or a `$` command). Cases carry JSON inputs and an expected cell that is a JSON value, `!ErrorType` (optionally `!ErrorType: message`), or `Ok`. Compare modes on the signature line (`~approx`, `~unordered`, `~matches`, `~struct`, `~decimal`) and a `:decimal` parameter type control how the result is checked. `@fixture` names a value reused across rows, or an opaque handle the harness builds fresh per case.
- Verification tasks in the build plan. Audit emits a deterministic task per VMD group and per file's scenario bundle. It never writes or edits these tasks, and it tells the planner which targets and scenarios already have author cases so it doesn't plan duplicate test tasks. Command scenarios run each step in a fresh per-scenario working directory; the harness looks for the built program in the common build locations and falls back to the command name on `PATH`.
- `ossature new -t vmd` and a VMD wizard for scaffolding a verification file.
- Requirement coverage in `ossature validate`. Groups and scenarios map to requirements by name, or explicitly with `@covers`. Requirement headings accept an optional `{#anchor}` slug that survives renaming, and a `.no-verify` class to mark a requirement as intentionally unverified. Plan tasks accept a `covers` field, which the planner sets on the test tasks it plans.
- `[test]` config section for the verification harness, with `command` to override the verify command (`{file}` expands to the harness path) and `require_coverage` to make validate fail on an uncovered requirement.

### Changed

- Build skips a cross-spec dependent when its upstream interface is unchanged, gated on the interface file's input hash. A dependent still rebuilds when the upstream rebuilt but left no extractable interface on disk.
- Moved to pydantic-ai 2.0. The Google provider prefix is now `google`, not `google-gla`; update any `[llm]` model string that used the old form.

### Fixed

- Incremental re-plan no longer drops the intra-spec dependencies of a preserved task. A one-line edit to a spec kept the untouched tasks but could break their `depends_on` links.
- The audit fixer's in-memory copy of a spec is refreshed after it edits the file, so a later stage in the same run reads the fixed content instead of the stale parse.
- The fixer's no-op reminder now reaches the model. It was assembled but never sent, so a fixer that made no edit got no nudge to try again.
- AMD files round-trip cleanly now. The title no longer doubles its `Architecture:` prefix, and a flow's fenced block no longer nests an extra fence on render.
- SMD bullets indented under a requirement keep their content without a stray leading dash.
- Section splitting in the SMD and AMD parsers is fence-aware, so a `##` line inside a code block no longer starts a new section.
- The planner raises a clear error on a dependency cycle instead of silently dropping specs from the build order.
- Verify and setup output is escaped before Rich renders it, so a program that prints `[` no longer crashes the run with a markup error.
- The "failed after N fix attempts" panel only shows when a task actually exhausted the fix loop, not when it failed for another reason.
- Ctrl-C at an audit confirmation aborts the run instead of counting as answering "No" and continuing.
- A corrupt audit cache or manifest file is disregarded and regenerated instead of crashing the run.
- `ossature new` checks for an existing target file before starting the wizard and exits cleanly instead of overwriting or failing partway.

## 0.1.1 - 2026-06-18

After a task's verify passes, a reviewer step now checks that the generated code actually does what the spec asked, not just that it compiles. An LLM reads the code against the task's spec requirements and the contracts declared for its components, and a failed review goes into the same fixer a verify failure does. It is on by default and turns off with `review = false` under `[build]`. This gives the AMD contracts a consumer; until now they were only a hint to the implementer, and now the generated code is checked against them.

A component's contracts apply only to the task that finalizes its file, so a task that just scaffolds a placeholder is not checked against, or told to satisfy, behavior a later task adds. The rest of this release is cleanup. A config field that was defined but never read now works, two options that were parsed but never used are gone, and there are a couple of default and rendering fixes.

### Added

- Post-task reviewer. When `[build] review` is on (the default), each task that passes verification is reviewed by an LLM against its spec requirements and declared contracts. A failed review enters the fix loop with the reviewer's findings, then re-verifies and re-reviews, up to `max_review_attempts` (default 2), after which the task fails like an exhausted verify loop. The reviewer runs only on tasks that have something to check and only when a task builds, so cached tasks are not re-reviewed.
- `review` and `max_review_attempts` fields in the `[build]` section.
- `reviewer` role in the `[llm]` section, falling back to the default `model` like the other roles.

### Changed

- A component's contracts are checked, and handed to the implementer, only for the task that finalizes its file. A scaffold task whose file a later task rewrites is not held to the finished component's contracts.
- Acceptance criteria render as plain `-` bullets instead of `- [ ]` checkboxes.
- `[output] dir` defaults to `output` when the `[output]` section is present but omits it. It previously defaulted to `.`, which put generated files in the project root.
- The unused `[test]` config section and the `[build] test` command were removed. Both were parsed but nothing ran them; a config that still sets them keeps loading and the keys are ignored.

### Fixed

- `[output] framework` is now read from the config. It was defined but never parsed, so setting it had no effect. It now reaches the planner and build prompts and the project brief.

## 0.1.0 - 2026-06-15

AMD components can now state how they should behave. A component lists a `**Contracts:**` block of short rules like "never mutates its input" or "raises KeyError when the id is unknown", or `**Contracts:** None` if there is nothing to state. The block is required, so existing `.amd` files need it added before they parse again.

The planner now adapts to your project's language instead of always using Rust and C examples, and it rejects plans that would build code before the source files exist. Tasks can also copy files from the context directory as-is, without sending them through the model.

When a task does not produce the files it was supposed to, build stops with a clear error instead of running a fix loop that cannot help.

### Added

- `**Contracts:**` block on AMD components, a required list of behavioral guarantees (preconditions, postconditions, invariants) or an explicit `None`. `ossature new -t amd` and the AMD wizard scaffold the field, and the extracted interface under `.ossature/context/interfaces/<SPEC>.md` gains a `## Declared Contracts` section so dependent specs see contracts next to the signatures.
- `source` field on `plan.toml` tasks. A task with `source = ["context://<path-or-glob>"]` and an empty `verify` copies the matched context files straight into the paired `outputs` paths without calling the LLM. `source` and `outputs` pair by index, and each may use at most one `*` or `**` wildcard. The planner emits these on its own for outputs that ship unchanged, such as binary assets and reference data.
- `max_output_tokens` in the `[build]` section sets the per-call output token limit for the implementer and fixer agents (default 32768). Raise it for tasks that produce very large source files.

### Changed

- The planner prompt is assembled from a per-language profile, so its first-choice verify command, scaffold manifests, forbidden build invocations, and worked examples match the project's `output.language`. Curated profiles exist for Python, Rust, JavaScript, TypeScript, Lua, and Zig; other languages use a generic fallback.
- Audit runs a deterministic check on planner-generated verify commands and rejects a plan whose verify invokes a build before the source it needs has been produced by that task or a `depends_on` predecessor. The check is per-language and stays off for languages without profile data, so it produces no false positives.
- Declared contracts are checked during audit and cross-spec audit, and the implementer is told to satisfy every one during build.
- `ossature validate` rejects duplicate component names within a spec's AMD files and warns about unrecognized `##` sections. The AMD parser accepts the `Interface`, `Contracts`, and `Depends on` markers in any order.
- The audit fixer can edit `.amd` files, not only `.smd` files.

### Fixed

- `ossature build` no longer enters a fix loop it cannot win when a task's implementer produces none of its expected outputs. It stops with a Missing Outputs error pointing at `.ossature/tasks/<id>-*/` and suggests simplifying the task, switching model, or rerunning `ossature retry --only <id>`.
- When a task expects outputs but the implementer replies with only prose and never calls `write_file`, build retries the implementation up to two more times with a stronger reminder instead of moving on.
- `ossature build` and `ossature retry` no longer crash with a traceback when a spec fails to parse. They print a message pointing you to `ossature validate`.

## 0.0.5 - 2026-05-13

Spec metadata now uses standard YAML frontmatter (`---` delimited) instead of the custom `@key: value` format. The `verify`, `setup`, and `test` fields are now lists of shell commands rather than single strings, improving readability for multi-step jobs. The pre-flight tool check was simplified: commands with `/` in the name are treated as project artifacts and skipped, fixing false positives for patterns like make `&& ./myapp`. The planner prompt now scopes `verify` to each task's own outputs, using lightweight checks for scaffold tasks that don't yet have buildable source.

### Changed

- `parse_smd` and `parse_amd` now expect a YAML frontmatter block at the top of the file. They reject specs with the old `@key: value` format.
- `render_smd` and `render_amd` emit a `---` delimited frontmatter block before the H1 title.
- `ossature new` scaffolds new specs using the new format.
- The fixer prompt was updated so the LLM knows to leave the frontmatter alone unless a finding requires editing it.
- `PlanTask.verify`, `PlannerTask.verify`, and `BuildConfig.{setup, verify, test}` are now `list[str]`. Bare strings in existing `plan.toml` and `ossature.toml` files get coerced on load, so older files keep working.
- `run_setup` and `run_verify` iterate the command list, run each step in its own shell, stop on the first non-zero exit, and prefix multi-step output with `$ <command>` headers so failures are self-describing.
- The planner prompt tells the LLM to emit `verify` as a list, scope each step to the task's own outputs, and use lightweight checks for scaffold-only tasks. New examples cover both the scaffold case and the dependent compile-and-run case.

### Fixed

- `ossature build`'s pre-flight tool check no longer flags binaries produced earlier in the same verify pipeline as missing PATH dependencies. This affected any `compile && ./run` pattern (gcc with a binary, make with `./binary`, cargo build with `target/release/x`, and so on) regardless of language or build system.
- The `[llm]` section is now checked against pydantic_ai when the config loads. Misspelled provider names (`anthrop:…`), missing `provider:` separators (`openai_gpt-5.5`), and unrecognized model names emit warnings with close-match suggestions instead of failing later with an opaque agent error. Validation is offline and warning-only, so newly released models still work even if pydantic_ai's known list is behind. Provider-side failures (e.g. an HTTP 404 for an invalid model name) now render a clear error panel that points back at the `[llm]` section instead of the generic "try a more capable model" message.

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
