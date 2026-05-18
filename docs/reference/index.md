# Reference

The reference section contains technical specifications. Use it when you need precise details rather than narrative: exact CLI flags, required config keys, field-by-field format definitions.

If you want to learn the concepts behind these formats, see the [Topic guides](../topics/index.md). If you want step-by-step instructions, see the [How-to guides](../how-to/index.md).

## Available reference pages

**[CLI](cli.md)**
All commands and their flags: `ossature init`, `validate`, `audit`, `build`, `retry`, `status`, `new`, `clean`, and more.

**[SMD Format](smd-format.md)**
The Spec Markdown format: frontmatter fields, required and optional sections, requirement subsection structure, examples.

**[AMD Format](amd-format.md)**
The Architecture Markdown format: frontmatter fields, component definitions, interface blocks, data models, external dependencies.

**[Configuration](configuration.md)**
All `ossature.toml` keys: `[project]`, `[output]`, `[build]`, `[llm]`, and per-role model overrides.

**[plan.toml](plan-toml.md)**
The build plan format: task fields, status values, dependency and injection fields, verify commands.

**[state.toml](state-toml.md)**
The build state format: per-task hash records, file ownership lists, and what each field means.

**[The .ossature/ directory](ossature-directory.md)**
The layout of the `.ossature/` working directory: what each file and subdirectory contains and which ones are safe to delete.
