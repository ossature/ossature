# 4. Review the Plan

**What you'll do:** Read through the generated build plan, check that the task breakdown and ordering make sense, and make any adjustments before building.

Previous step: [Validate and audit](03-validate-and-audit.md). Next step: [Build and iterate](05-build-and-iterate.md).

## Read the plan

After audit, the build plan is written to `.ossature/plan.toml`. Open it and read through it before building.

The markman plan has 22 tasks across three specs. Here's what a couple of tasks look like:

```toml
[[task]]
id = "001"
spec = "STORAGE"
title = "Storage: Data Types & Errors"
description = "Define the core Bookmark struct and StorageError enum."
outputs = ["src/storage.rs"]
depends_on = []
spec_refs = ["Overview", "Requirements > Add Bookmark", ...]
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

Things worth checking:

- Dependencies are in a reasonable order and no task comes before something it needs.
- Tasks are appropriately sized. A task that touches ten files is probably too broad; a task that only adds one function to an existing file is fine.
- `spec_refs` lists the right sections of the spec. This controls what content goes into each task's prompt.
- Verify commands will actually catch problems at the point in the build where that task runs.

!!! tip "You should see STORAGE tasks first"
    Because CLI and WEBUI both declare `depends: [STORAGE]`, the plan should show all STORAGE tasks completing before any CLI or WEBUI tasks begin. If the ordering looks wrong, check the `depends` fields in your SMD frontmatter.

## Edit the plan

The plan is human-editable. You can reorder tasks, change verify commands, add notes, or set a task's status to `skipped`. Your changes are respected when you run `ossature build`.

To discard the plan and regenerate from scratch, use:

```bash
ossature audit --replan
```

!!! warning
    `--replan` discards the existing plan entirely, including any manual edits you have made. Only use it if you want to start the planning from scratch.

## Next step

[Build and iterate](05-build-and-iterate.md) -- Run the build and see the generated code appear in `output/`.
