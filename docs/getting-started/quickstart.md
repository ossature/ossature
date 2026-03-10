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

You can also create architecture files (`.amd`) that describe the internal structure - components, data models, interfaces. If you skip them, the LLM infers the architecture during audit.

```bash
ossature new my-feature -t amd
```

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
