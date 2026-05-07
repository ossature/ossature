# AMD Format

AMD (Architecture Markdown) defines the internal structure of a spec: components, their file paths, interfaces, data models, and dependencies. It's optional. If you skip it, the LLM infers the architecture during audit.

## When to Use AMD

Write an AMD when you want control over how the code is organized. Without an AMD, the LLM decides the file structure, module boundaries, and interfaces on its own. That's fine for simple projects, but for anything non-trivial you'll probably want to lay out the architecture yourself.

## Structure

````markdown
---
spec: EXPENSE_TRACKER
status: draft
---

# Architecture: Expense Tracker

## Overview

Three Python modules: a storage layer for JSON persistence,
a core module for business logic, and a CLI entry point.

## Components

### Storage

@path: src/spenny/storage.py

Handles reading and writing the expenses.json file.

**Interface:**

```python
class ExpenseRecord(TypedDict):
    id: int
    date: str
    amount: str
    category: str
    description: str

class ExpenseData(TypedDict):
    next_id: int
    expenses: list[ExpenseRecord]

def load(path: str = "expenses.json") -> ExpenseData: ...
def save(data: ExpenseData, path: str = "expenses.json") -> None: ...
```

**Depends on:** None

### Core

@path: src/spenny/core.py

Business logic. All functions are pure.

**Interface:**

```python
def add_expense(data: ExpenseData, amount: Decimal,
    category: str, description: str = "",
    date: str | None = None) -> tuple[ExpenseData, ExpenseRecord]: ...

def list_expenses(data: ExpenseData,
    category: str | None = None) -> list[ExpenseRecord]: ...

def delete_expense(data: ExpenseData,
    expense_id: int) -> ExpenseData: ...
```

**Depends on:** Storage

### CLI

@path: src/spenny/cli.py

Entry point. Parses arguments, calls core functions, formats
output. Only module that prints to stdout or calls sys.exit.

**Depends on:** Core, Storage

## Data Models

### expenses.json

```json
{
  "next_id": 4,
  "expenses": [
    {
      "id": 1,
      "date": "2026-03-01",
      "amount": "12.50",
      "category": "Food",
      "description": "Lunch at cafe"
    }
  ]
}
```

## Flow

```
CLI (argparse)
  ├── add    -> core.add_expense()    -> storage.save()
  ├── list   -> core.list_expenses()  -> print table
  ├── delete -> core.delete_expense() -> storage.save()
  └── summary -> core.summarize()     -> print summary
```

## Dependencies

- Python 3.14+ standard library only
- uv: project management and packaging
````

## Metadata Fields

These go inside the frontmatter block at the top of the file.

| Field | Required | Description |
|-------|----------|-------------|
| `spec` | Yes | The `id` of the SMD this architecture describes. |
| `status` | Yes | `draft`, `review`, `approved`, or `implemented` |

## Sections

**Overview** - Brief description of the architecture. A sentence or two.

**Components** - The building blocks. Each component is an H3 heading with:

- `@path` - where the file lives relative to the output directory
- A description of what the component does
- An **Interface** code block showing the public API (types, function signatures, no implementations)
- A **Depends on** line listing other components this one uses

**Data Models** - Shared data structures. Usually shown as code blocks with example data or type definitions.

**Flow** - How data moves through the components. Can be a diagram, a list, or just prose.

**Dependencies** - External libraries or tools the project needs.

## Component Interfaces

The interface code blocks are important. During audit, Ossature extracts these and uses them as contracts between specs. When building tasks for the API spec that depends on AUTH, the API tasks see AUTH's interface signatures but not AUTH's implementation code.

If you include interface definitions, the LLM will implement them exactly. If you leave them out, the LLM will infer appropriate signatures from the spec.

## Multiple AMDs Per Spec

A single spec can have multiple AMD files. Each one describes a different facet of the system:

```
specs/
├── database.smd
├── database-models.amd      # Just the data models
└── database-migrations.amd  # Just the migration system
```

Their contents are merged during audit. Component names must be unique across all AMDs for the same spec.

## Next Steps

- [SMD Format](smd.md) - The spec format
- [Overview](overview.md) - How SMD and AMD work together
