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

**Contracts:** None

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

**Contracts:**

- add_expense returns a new ExpenseData and never mutates the data passed in
- delete_expense raises KeyError when no expense has the given id
- Amounts are stored as decimal strings, never floats, so currency math stays exact

**Depends on:** Storage

### CLI

@path: src/spenny/cli.py

Entry point. Parses arguments, calls core functions, formats
output. Only module that prints to stdout or calls sys.exit.

**Interface:**

```python
def main(argv: list[str] | None = None) -> int: ...
```

**Contracts:** None

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

- Python 3.14+: standard library only
- uv: project management and packaging
````

## Metadata Fields

These go inside the frontmatter block at the top of the file.

| Field | Required | Description |
|-------|----------|-------------|
| `spec` | Yes | The `id` of the SMD this architecture describes. |
| `status` | Yes | `draft`, `review`, `approved`, `implemented`, or `deprecated` |

## Sections

**Overview** - Brief description of the architecture. A sentence or two.

**Components** - The building blocks. Each component is an H3 heading with:

- `@path` - where the file lives relative to the output directory
- A description of what the component does
- An **Interface** code block showing the public API (types, function signatures, no implementations)
- A **Contracts** list of behavioral guarantees the implementation must uphold, like preconditions, postconditions, and invariants, or an explicit `None`
- A **Depends on** line listing other components this one uses

**Data Models** - Shared data structures. Usually shown as code blocks with example data or type definitions.

**Flow** - How data moves through the components. Can be a diagram, a list, or just prose.

**Dependencies** - External libraries or tools the project needs.

## Component Interfaces

The interface code blocks are important. During audit, Ossature extracts these and uses them as the boundary between specs. When building tasks for the API spec that depends on AUTH, the API tasks see AUTH's interface signatures but not AUTH's implementation code.

If you include interface definitions, the LLM will implement them exactly. If you leave them out, the LLM will infer appropriate signatures from the spec.

## Contracts

An interface signature says what a component is called and what types pass through it. It says nothing about how the component must behave. A `**Contracts:**` block captures that behavior as a short list of preconditions, postconditions, and invariants the implementation must uphold. The Core component above uses one to state that `add_expense` never mutates its input and that amounts stay decimal strings.

Add them where the signature alone leaves room for an implementation that type-checks but does the wrong thing, a function that returns a hardcoded value, mutates an argument it should leave alone, or quietly skips an error case. Every component states its position either way. When the signature and description already make the behavior clear, write `**Contracts:** None`, the same explicit no that `**Depends on:** None` gives, so a reader can tell a considered decision from a forgotten section. The Storage and CLI components above use it.

Contracts can look like SMD requirements, since both state behavior. The split follows what each document can talk about. A requirement describes what the system does for its user, in terms the user can observe. A contract describes what one component guarantees to the rest of the code, in terms of the functions and types the AMD itself introduced. "Deleting a missing expense prints an error and exits with code 1" belongs in the SMD. "delete_expense raises KeyError when no expense has the given id" belongs here, because the SMD has no delete_expense to talk about. When a behavior fits both documents, state it in the SMD and keep the contract for whatever the requirement leaves open. Stating the same rule in both places invites the copies to drift apart.

Contracts are read at every stage. During audit, the auditor checks them against the spec requirements and flags any that contradict a requirement or that cannot all hold on the same component, and the cross-spec audit compares them with what dependent specs expect. During build, a task that finalizes a component gets its contracts in the implementer's prompt and is told to satisfy every one, and after the task builds the post-task reviewer checks the generated code against them. A task that only scaffolds a file a later task rewrites sees the interface but not the contracts, which belong to the task that finalizes the file. They also cross spec boundaries: a spec that depends on this one sees the contracts next to the interface signatures.

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
