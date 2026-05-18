# 2. Write Your Specs

**What you'll do:** Plan the module breakdown for markman, create the SMD files, and write the requirements for each module.

Previous step: [Initialize the project](01-init-and-config.md). Next step: [Validate and audit](03-validate-and-audit.md).

## Plan the module breakdown

Before writing any spec files, think about how your project breaks down into modules and what depends on what. For markman, we need three specs:

- **STORAGE** - SQLite persistence layer, no dependencies
- **CLI** - command-line interface, depends on STORAGE
- **WEBUI** - read-only web interface, depends on STORAGE

The `depends` field creates an explicit ordering. STORAGE gets built first because CLI and WEBUI both need it.

## Create the spec files

```bash
ossature new storage
ossature new cli
ossature new webui
```

You should now have three files in `specs/`: `storage.smd`, `cli.smd`, and `webui.smd`.

## Write the SMD files

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

This tells Ossature that CLI tasks should come after STORAGE tasks in the build plan, and that CLI prompts should include STORAGE's public interface.

!!! tip "If something goes wrong"
    If you get a parse error when running validate later, check that your frontmatter uses the correct field names (`id`, `status`, `priority`, `depends`) and that the requirement subsections start with `### `.

## When to write an AMD

Architecture files (`.amd`) are optional. They let you define the internal structure of a module: components, file paths, data models, and public interfaces. If you skip them, the LLM infers the architecture during audit.

For markman, we skipped AMDs entirely. The specs are detailed enough that the LLM can figure out the structure on its own. If you know exactly what shape your system should take, writing an AMD gives the LLM less room to improvise. See the [AMD Format](../reference/amd-format.md) reference.

See the [SMD Format](../reference/smd-format.md) reference for the full format, and the [complete markman specs](https://github.com/ossature/ossature-examples/tree/master/markman/specs) for the full example.

## Next step

[Validate and audit](03-validate-and-audit.md) -- Check your specs for structural issues and send them to the LLM for review.
