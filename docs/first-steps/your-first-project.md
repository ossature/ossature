# Your First Project

You will create a project, write one spec, and watch Ossature generate code from it. The whole sequence takes about five minutes and uses a single spec with no architecture file.

## Create a project

```bash
ossature init hello
cd hello
```

Open `ossature.toml` and update the model to match your LLM provider:

```toml
[project]
name = "hello"
version = "0.0.1"
spec_dir = "specs"

[output]
dir = "output"
language = "python"

[llm]
model = "anthropic:claude-sonnet-4-6"
```

Export the matching API key before running any commands. For `anthropic:...` use `ANTHROPIC_API_KEY`; for `openai:...` use `OPENAI_API_KEY`. See [Configuration](../reference/configuration.md) for all provider options.

## Write a spec

```bash
ossature new greeting
```

This creates `specs/greeting.smd`. Open it and fill it in. A spec must include all of: overview, goals, requirements (each with Accepts/Returns), examples (each with Input/Output code blocks), constraints, non-goals, and acceptance criteria. Here is a complete minimal spec:

````markdown
---
id: GREETING
status: draft
priority: high
depends: []
---

# Greeting

## Overview

A greeting function that takes a name and returns a formatted message.

## Goals

- Return a personalized greeting string for any non-empty name

## Non-Goals

- Internationalization or locale-specific formatting

## Requirements

### say_hello

A function that produces a greeting message.

**Accepts:** name (string, non-empty)

**Returns:** string

**Errors:**

- name is empty -> raises ValueError with message "name must not be empty"

## Constraints

- The greeting format must be "Hello, {name}!"

## Examples

### Basic greeting

**Input:**

```python
say_hello("Alice")
```

**Output:**

```
"Hello, Alice!"
```

## Acceptance Criteria

- say_hello("Alice") returns "Hello, Alice!"
- say_hello("") raises ValueError
````

The requirement section matters most. Each requirement should say what it accepts, what it returns, and what errors it raises. Vague requirements produce vague code.

## Validate

```bash
ossature validate
```

This parses your specs and checks for structural issues: missing required sections, broken `depends` references, cycles in the dependency graph. No LLM calls happen here.

Validation catches problems like missing `**Accepts:**` or `**Returns:**` fields in requirements, missing `**Input:**` or `**Output:**` markers in examples, and `depends` targets that don't match any spec `id`. Fix any errors it reports, then re-run until it passes clean.

## Audit

```bash
ossature audit
```

The LLM reads your spec, reviews it for gaps and ambiguity, and writes a build plan to `.ossature/plan.toml`. Open the plan and read through it before building. You can reorder tasks, adjust verify commands, or add notes. The plan is yours to edit.

## Build

```bash
ossature build
```

Ossature works through each task in the plan. For each one it assembles a prompt, sends it to the LLM, writes the generated files to `output/`, and runs a verify command. If verification fails, a separate fixer agent reads the error output and tries to repair the code.

!!! note "Where to next?"
    The [Tutorials](../tutorials/index.md) section walks through the same sequence with a real multi-spec project, more detail at each step, and guidance for when things go wrong.
