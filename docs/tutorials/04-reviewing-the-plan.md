# 4. Review the Plan

Previous step: [Validate and audit](03-validate-and-audit.md). Next step: [Build and iterate](05-build-and-iterate.md).

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

Things worth checking: whether dependencies make sense and tasks are in a reasonable order, whether tasks are too broad (touching too many files) or too narrow, whether `spec_refs` is pulling the right spec sections into each task's prompt, and whether verify commands will actually catch problems.

The plan is human-editable. You can reorder tasks, change verify commands, add notes, or set a task's status to `skipped`. Your changes are respected when you run `ossature build`.

To discard the plan and regenerate from scratch, use `ossature audit --replan`.
