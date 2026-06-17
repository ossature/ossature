# Workflow Guide

This page walks through a full project from init to generated code using [markman](https://github.com/ossature/ossature-examples/tree/master/markman), a Rust bookmark manager with a CLI and a read-only web UI. The Quick Start covers the same commands but without the context of why each step matters or what to do when things go wrong.

## 1. Initialize the Project

```bash
ossature init markman
cd markman
```

This creates an `ossature.toml` and a `specs/` directory. Open the config and set it up:

```toml
[project]
name = "markman"
version = "0.0.1"
spec_dir = "specs"

[output]
dir = "output"
language = "rust"

[build]
setup = ["cargo init --name markman"]
verify = ["cargo check"]

[llm]
model = "anthropic:claude-haiku-4-5-20251001"
```

The `[build]` section is optional. `setup` runs once before the first task (here it initializes a Cargo project in the output directory). Each task in the generated plan carries its own `verify` command, chosen by the planner for that task. The `verify` list under `[build]` is read before the build starts, where Ossature checks that the tools its commands need are present on PATH. It is not used as the per-task verify. See [Configuration](../configuration/ossature-toml.md) for all available options.

## 2. Write Your Specs

Before writing any spec files, think about how your project breaks down into modules and what depends on what. For markman, we need three specs:

- **STORAGE** - SQLite persistence layer, no dependencies
- **CLI** - command-line interface, depends on STORAGE
- **WEBUI** - read-only web interface, depends on STORAGE

The `depends` field creates an explicit ordering. STORAGE gets built first because CLI and WEBUI both need it.

Create the spec files:

```bash
ossature new storage
ossature new cli
ossature new webui
```

### Writing SMD files

Each `.smd` file starts with a YAML frontmatter block, then describes what the module should do. Here's an abbreviated version of the storage spec:

```markdown
---
id: STORAGE
status: draft
priority: critical
depends: []
---

# Storage

## Overview

SQLite-backed persistence layer for bookmarks. Each bookmark has a URL,
description, and comma-separated tags. This module owns all database
interaction; the CLI and web UI use it exclusively.

## Requirements

### Add Bookmark

Inserts a new bookmark record.

**Accepts:** conn (Connection), url (string, non-empty), desc (string,
may be empty), tags (string, comma-separated, may be empty)

**Returns:** `Result<i64, StorageError>` - the integer row id of the
newly inserted bookmark on success

**Errors:**

- Empty url -> returns `StorageError::InvalidInput("url must not be empty")`
- URL already exists -> returns `StorageError::Duplicate(url)`
- Any other database error -> returns `StorageError::Db(reason)`
```

Being specific matters. Each requirement says what it accepts, what it returns, and what happens on every error case. "Handle invalid input" leaves too much to interpretation. "Empty url returns `StorageError::InvalidInput`" does not.

The CLI spec declares its dependency on storage:

```markdown
---
id: CLI
status: draft
priority: high
depends: [STORAGE]
---

# CLI
```

This tells Ossature that CLI tasks should come after STORAGE tasks in the build plan, and that the CLI's prompts should include STORAGE's public interface.

See the [SMD Format](../specs/smd.md) reference for the full format, and the [complete markman specs](https://github.com/ossature/ossature-examples/tree/master/markman/specs) for the full example.

### When to write an AMD

Architecture files (`.amd`) are optional. They let you define the internal structure of a module: components, file paths, data models, and public interfaces. If you skip them, the LLM infers the architecture during audit.

For markman, we skipped AMDs entirely. The specs are detailed enough that the LLM can figure out the structure on its own. If you know exactly what shape your system should take, writing an AMD gives the LLM less room to improvise. See the [AMD Format](../specs/amd.md) reference.

## 3. Validate

Check that your specs are structurally correct:

```bash
ossature validate
```

This parses every `.smd` and `.amd` file and checks that all `depends` targets exist (so `[STORAGE]` actually refers to a spec with `id: STORAGE`), all `spec` references in AMDs resolve to real SMDs, there are no duplicate component names within a spec, and there are no cycles in the dependency graph. No LLM is involved.

If there are errors, fix them and re-run until validation passes clean. Common issues at this stage are `depends` targets that don't match any `id`, requirement sections missing `**Accepts:**` or `**Returns:**`, and example sections missing `**Input:**` or `**Output:**` subsections.

## 4. Audit

Audit sends your specs to the LLM for semantic review and generates the build plan:

```bash
ossature audit
```

It runs through several stages: computing checksums and checking what changed since the last audit, reviewing each changed spec for ambiguity, contradictions, gaps, and feasibility issues, running a cross-spec audit if there are multiple specs, generating a project brief and per-spec briefs, extracting or inferring interface signatures, and finally generating the build plan.

### Reviewing findings

The audit produces findings at three severity levels: errors (will likely cause build failures, fix these), warnings (potential problems worth considering), and info (observations you can usually ignore).

For example, auditing the markman storage spec produced a warning about an inconsistency between the example output and the requirement text:

!!! warning "Ambiguous timestamp format"
    The spec shows `created_at` in the example output as ISO format (`2026-01-01T00:00:00`), but the requirement states it is stored as `YYYY-MM-DD HH:MM:SS` (space-separated, as produced by SQLite `datetime('now')`). This could cause implementations to differ in timestamp format.

The example and the requirement disagreed on the format. The fix was to update the example output to use the space-separated format that SQLite actually produces:

````markdown
**Output:**

```
[Bookmark { id: 1, url: "https://example.com", desc: "Example site",
tags: "example,test", created_at: "2026-01-01 00:00:00" }]
```
````

By default, the audit auto-fixes errors without prompting — it edits your spec files directly, re-audits, and repeats up to 3 cycles per spec. Auto-fix only runs on a spec that has a fixable error, and when it runs the fixer also addresses any warnings on that spec. Info findings are left alone while an error or warning is present. Use `--interactive` if you want to approve each fix, or `--no-fix` to skip fixing entirely. After audit completes, all findings are saved to `.ossature/audit-report.md`.

### Incremental audits

On subsequent runs, audit only re-processes specs whose files have changed. If you edit `storage.smd` but leave `cli.smd` and `webui.smd` untouched, only STORAGE gets re-audited and re-planned. The planner sees a diff of what changed and keeps unaffected tasks stable. Tasks that produce the same output files as before carry over their build status, so a minor edit doesn't throw away progress. Tasks for CLI and WEBUI are preserved entirely. See [Incremental Re-Planning](../advanced/build-system.md#incremental-re-planning) for details.

## 5. Review the Plan

After audit, the build plan is written to `.ossature/plan.toml`. Read it before building.

The markman plan has 22 tasks across three specs. Here's what a couple of tasks look like:

```toml
[[task]]
id = "001"
spec = "STORAGE"
title = "Storage: Data Types & Errors"
description = "Define the core Bookmark struct and StorageError enum."
outputs = ["src/storage.rs"]
depends_on = []
spec_refs = ["Overview", "Add Bookmark", ...]
status = "pending"
verify = ["cargo check"]

[[task]]
id = "002"
spec = "STORAGE"
title = "Storage: Database Initialization"
outputs = ["src/storage.rs"]
depends_on = ["001"]
inject_files = ["src/storage.rs"]
status = "pending"
verify = ["cargo check"]
```

Things worth checking: whether dependencies make sense and tasks are in a reasonable order, whether tasks are too broad (touching too many files) or too narrow, whether `spec_refs` is pulling the right spec sections into each task's prompt, and whether verify commands will actually catch problems.

The plan is human-editable. You can reorder tasks, change verify commands, add notes, or set a task's status to `skipped`. Your changes are respected when you run `ossature build`.

To discard the plan and regenerate from scratch, use `ossature audit --replan`.

## 6. Build

When the plan looks right:

```bash
ossature build
```

For each task, Ossature assembles a prompt from the project brief, relevant spec sections, interface files, and output from earlier tasks. The LLM generates code and writes files to the output directory. After each task, the verify command runs. If verification fails, a separate fixer agent reads the errors and tries to repair the code, up to `max_fix_attempts` times (default 3). Once verification passes, a reviewer reads the generated code against the spec and the component contracts and flags anything that compiles but doesn't do what the spec asked; a failed review goes through the same fixer. Turn this off with `review = false` under `[build]`.

By default the build continues silently on success and pauses on failure with a prompt: retry, skip, or quit. Other modes:

```bash
ossature build --step    # pause after every task for approval
ossature build --auto    # run to completion, stop on first failure
ossature build --auto --skip-failures  # run everything, skip failures
```

For a first build, `--step` is useful so you can inspect the output before continuing.

### If something fails

`ossature retry` re-runs failed tasks:

```bash
ossature retry                # re-run all failed tasks
ossature retry --from 007     # redo everything from task 007 onwards
ossature retry --only 005     # re-run task 005 and all its dependents
```

Check progress at any point with `ossature status`.

See [The Build System](../advanced/build-system.md) for the full details on the build loop, fix loop, invalidation, and retry.

## 7. Iterate

After a build, you'll usually want to change something. Edit the spec, then run the same sequence again:

```bash
ossature validate
ossature audit
ossature build
```

The build is incremental. If you change `storage.smd`, only STORAGE tasks and any downstream tasks that reference STORAGE's interface get rebuilt. CLI and WEBUI tasks stay untouched if STORAGE's public interface didn't change.

## Summary

```
ossature init       create project
edit specs          describe what to build
ossature validate   fix structural issues (loop until clean)
ossature audit      LLM review + plan generation (fix errors, loop until clean)
review plan.toml    check task order, granularity, verify commands
ossature build      generate code task by task
ossature retry      re-run failures
edit specs          iterate
```

The specs are your source of truth. The plan is your review checkpoint. When something breaks, you fix the spec or the plan and rebuild instead of starting over.

## Next Steps

- [SMD Format](../specs/smd.md) - Full spec format reference
- [AMD Format](../specs/amd.md) - Architecture format reference
- [Configuration](../configuration/ossature-toml.md) - All config options
- [Commands](../cli/commands.md) - CLI reference
- [Build System](../advanced/build-system.md) - Build loop, invalidation, retry internals
