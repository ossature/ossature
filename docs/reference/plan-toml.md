# plan.toml

`plan.toml` is the central artifact. It lists every task in order with dependencies, spec references, and status.

```toml
[meta]
generated_at = "2026-03-10T18:09:18Z"
total_tasks = 8
specs = ["EXPENSE_TRACKER"]

[[task]]
id = "001"
spec = "EXPENSE_TRACKER"
title = "Project Config & Package Scaffold"
description = "Create pyproject.toml with project metadata..."
outputs = ["pyproject.toml", "src/spenny/__init__.py"]
depends_on = []
spec_refs = ["Goals", "Constraints"]
arch_refs = ["Dependencies"]
status = "pending"
verify = ["uv run python -c 'import spenny'"]

[[task]]
id = "002"
spec = "EXPENSE_TRACKER"
title = "Storage Layer"
outputs = ["src/spenny/storage.py"]
depends_on = ["001"]
inject_files = ["pyproject.toml", "src/spenny/__init__.py"]
status = "pending"
verify = ["uv run python -c 'from spenny.storage import load, save'"]
```

`verify` is a list of shell commands. Each step runs in its own shell, in order, and the task fails on the first non-zero exit. Multi-step pipelines stay readable as a list rather than a long `&&`-chained string:

```toml
verify = [
    "make clean",
    "make CFLAGS='-std=c99 -Wall -Wextra -pedantic'",
    "./myapp --help > /tmp/help.txt",
    "grep -q -- '--help' /tmp/help.txt",
]
```

A bare string still loads for backwards compatibility, so `verify = "make"` is treated the same as `verify = ["make"]`.

The plan is human-readable and human-editable. After `ossature audit` generates it, you can reorder tasks, add notes, skip tasks, or insert manual steps before running `ossature build`.

Key fields on each task:

- `depends_on` - which tasks must complete first
- `spec_refs` - which spec sections to include in the prompt
- `arch_refs` - which architecture sections to include
- `inject_files` - output files from earlier tasks that this task needs to see
- `verify` - command to run after generation to check the output
- `context_files` - files from the context directory to include
