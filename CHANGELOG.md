# Changelog

All notable changes to Ossature are documented here.

This project follows [Semantic Versioning](https://semver.org/).

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
