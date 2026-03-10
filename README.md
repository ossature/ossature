# Ossature

[![CI](https://github.com/ossature/ossature/actions/workflows/ci.yml/badge.svg)](https://github.com/ossature/ossature/actions/workflows/ci.yml)

Most software projects can be described well enough to generate most of the code, if you give the problem enough structure upfront.

Ossature is a toolkit that takes that idea seriously.

You write a specification, optionally lay out the architecture, and Ossature breaks it down into a build plan that gets executed step by step with an LLM doing the code generation under tight constraints.

This is not "vibe coding" where you throw a prompt at a model and hope for the best. It's the opposite. The specs are your source of truth, you review the plan before anything gets built, and when something breaks at step 14 of 30 you fix that step and keep going instead of starting over. When requirements change you update the spec, and Ossature figures out what needs to be regenerated.

*Ossature* (pronounced **OSS-uh-cher**) means the underlying framework or skeleton of a structure — the bones that hold everything together before the walls go up.

## How it works

A project is a collection of spec files (`.smd`) and optional architecture files (`.amd`) that describe what you're building. Ossature discovers these files, builds a dependency graph between them, and processes them through three stages:

1. `ossature validate` - parses your specs and checks that everything is structurally sound. No LLM involved.
2. `ossature audit` - sends your specs to an LLM for review, generates context files and a build plan. The plan is a TOML file you can read and edit before anything gets built.
3. `ossature build` - executes the plan task by task, calling the LLM to generate code for each one. Each task produces a small number of files, gets verified, and if verification fails there's a fix loop that tries to repair it.

All state lives in a `.ossature/` directory. The build is incremental, so if you change a spec, only the affected tasks get rebuilt. Interface files act as boundaries between specs so internal changes that don't affect the public surface don't cascade to downstream specs.

## Install

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ossature/ossature.git
cd ossature
uv sync
```

You'll also need an API key for your LLM provider. Set it as an environment variable, for example `ANTHROPIC_API_KEY` for Anthropic models.

## Getting started

Initialize a new project:

```bash
ossature init myproject
cd myproject
```

This creates a `ossature.toml` config file and a `specs/` directory. The config looks like this:

```toml
[project]
name = "myproject"
version = "0.0.1"
spec_dir = "specs"

[output]
dir = "output"
language = "python"

[llm]
model = "anthropic:claude-sonnet-4-6"
```

Create a spec file:

```bash
ossature new my-feature
```

Spec files use a markdown-based format with some metadata fields at the top. A minimal spec looks something like:

```markdown
# My Feature

@id: MY_FEATURE
@status: draft
@priority: high
@depends: []

## Overview

A short description of what this module does.

## Requirements

### Some Requirement

What the feature should do, what it accepts, what it returns,
what errors it should handle.

## Constraints

- Any constraints or rules the implementation should follow
```

You can also write architecture files (`.amd`) that describe the internal structure of a spec, things like components, data models, interfaces. These are optional. If you skip them, the LLM will infer the architecture during audit.

Once you have your specs ready, validate them:

```bash
ossature validate
```

Then audit. This is where the LLM reviews your specs for ambiguity, gaps, and feasibility, then generates a build plan:

```bash
ossature audit
```

The plan gets written to `.ossature/plan.toml`. You should look at it before building. You can reorder tasks, add notes, skip things. When you're happy with it:

```bash
ossature build
```

By default, the build pauses on failures and gives you the option to retry, skip, or quit. You can also run `ossature build --auto` to run without pausing, or `ossature build --step` to pause after every task.

If something fails you can use `ossature retry` to re-run just the failed tasks, or `ossature retry --from 007` to redo everything from a specific task onwards.

Check where things stand at any point with `ossature status`.

## LLM configuration

The default model applies to all roles, but you can override per role:

```toml
[llm]
model = "anthropic:claude-sonnet-4-6"
audit = "anthropic:claude-opus-4-6"
fixer = "anthropic:claude-opus-4-6"
```

Ollama models are supported too. Set the model to something like `ollama:llama3` and add the base URL:

```toml
[llm]
model = "ollama:llama3"
ollama_base_url = "http://localhost:11434/v1"
```

## License

MIT
