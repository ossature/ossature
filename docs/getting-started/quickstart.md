# Quick Start

## Create a Project

```bash
ossature init myproject
cd myproject
```

This creates a `ossature.toml` config and a `specs/` directory. The config looks like:

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

## Write a Spec

Create a spec file:

```bash
ossature new my-feature
```

This creates `specs/my-feature.smd`. Open it and describe what you want to build. Here's what a minimal spec looks like:

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

You can also create architecture files (`.amd`) that describe the internal structure, components, data models, and interfaces. If you skip them, the LLM infers the architecture during audit. But if you know what shape your system should take, writing one upfront gives the LLM less room to improvise.

```bash
ossature new my-feature -t amd
```

An AMD file links back to its spec via `@spec` and breaks the system down into concrete pieces. Here's a template:

```markdown
# Architecture: My Feature

@spec: MY_FEATURE
@status: draft

## Overview

How the system is structured at a high level. Which modules exist,
what role each one plays, how they connect.

## Components

### Component Name

@path: src/myproject/component.py

What this component does and what it's responsible for.

**Interface:**

```python
def do_something(input: str) -> Result: ...
```

**Depends on:** None

## Data Models

### Some Model

```json
{
  "id": 1,
  "name": "example"
}
```

## Flow

```
Entry point
  ├── action_a -> component.do_something()
  └── action_b -> other_component.handle()
```

## Dependencies

- some-library 2.x: what it's used for
```

The `Components` section is where most of the detail goes. Each component gets a `@path` (where it will live in your project), a description, an interface showing its public API, and a list of other components it depends on. You can define as many components as you need.

## Validate

Check that your specs are well-formed:

```bash
ossature validate
```

This parses everything and checks for structural issues. No LLM calls.

## Audit

Send your specs to the LLM for review. This catches ambiguity, gaps, and feasibility issues, then generates a build plan:

```bash
ossature audit
```

The plan gets written to `.ossature/plan.toml`. You should read it before building. You can reorder tasks, add notes, or skip things you don't want.

## Build

When the plan looks right:

```bash
ossature build
```

By default the build pauses on failures and lets you retry, skip, or quit. You can also run `ossature build --auto` to run without pausing, or `ossature build --step` to pause after every task for approval.

## If Something Fails

Use `ossature retry` to re-run just the failed tasks:

```bash
ossature retry
```

Or redo everything from a specific task onwards:

```bash
ossature retry --from 007
```

Check the current state at any point:

```bash
ossature status
```

## Next Steps

- [Workflow Guide](workflow.md) - Full walkthrough from init to build with a real example
- [SMD Format](../specs/smd.md) - Learn the spec format
- [AMD Format](../specs/amd.md) - Define architecture explicitly
- [Configuration](../configuration/ossature-toml.md) - Customize your project
- [Commands](../cli/commands.md) - All available commands
