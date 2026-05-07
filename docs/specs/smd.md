# SMD Format

SMD (Spec Markdown) is a Markdown-based format for defining what your system should do. It's written for both humans and the LLM to understand.

## Structure

An SMD file starts with a YAML frontmatter block delimited by `---`, followed by an H1 title and standard Markdown sections.


````markdown
---
id: EXPENSE_TRACKER
status: draft
priority: high
depends: []
---

# Expense Tracker

## Overview

A command-line expense tracker that stores expenses in a local
JSON file. Users can add expenses, list them with optional
filters, and generate a spending summary grouped by category.

## Goals

- Provide a fast, no-frills CLI for tracking personal expenses
- Persist data in a single human-readable JSON file
- Work with Python 3.14+ using only the standard library

## Non-Goals

- GUI or web interface
- Database backends
- Charts or visualizations

## Requirements

### Add an Expense

Record a new expense with an amount, category, and optional
description.

**Accepts:** amount (positive number), category (non-empty string),
description (optional string)

**Returns:** The created expense record with its assigned ID

**Errors:**

- Amount is zero or negative -> print error and exit with code 1
- Category is empty -> print error and exit with code 1

### List Expenses

Display all recorded expenses in a formatted table. Optionally
filter by category and/or date range.

**Accepts:** category (optional), start_date (optional),
end_date (optional)

**Returns:** A table with columns: ID, Date, Category, Amount,
Description

## Constraints

- No third-party dependencies
- Dates stored as ISO 8601 strings
- Exit code 0 on success, 1 on error

## Examples

### Adding an Expense

**Input:**

```
tracker add --amount 12.50 --category "Food"
```

**Output:**

```
Expense added: #1 — $12.50 [Food]
```

## Acceptance Criteria

- `add` creates an expense and writes it to the JSON file
- `list` displays all expenses in a formatted table
- Invalid inputs produce clear error messages and exit code 1
````

## Metadata Fields

These go inside the frontmatter block at the top of the file.

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier for this spec. Used in `depends` references and AMD `spec` links. Conventionally UPPER_SNAKE_CASE. |
| `status` | Yes | `draft`, `review`, `approved`, or `implemented` |
| `priority` | Yes | `low`, `medium`, `high`, or `critical` |
| `depends` | Yes | List of spec IDs this spec depends on. Empty list `[]` if none. |

The `depends` field creates edges in the spec dependency graph. When you write `depends: [AUTH, DATABASE]`, it means this spec assumes those other specs are already implemented.

## Sections

All sections are optional except Requirements. The LLM uses whatever you provide.

**Overview** - High-level description of what this module does. A few sentences is usually enough.

**Goals** - What this module aims to achieve.

**Non-Goals** - What it explicitly does not do. This helps the LLM avoid scope creep.

**Requirements** - The core of the spec. Each requirement is an H3 heading under the Requirements section. Describe what the feature accepts, what it returns, and what errors it should handle.

**Constraints** - Technical limitations or rules the implementation must follow.

**Examples** - Input/output examples. These are very helpful for the LLM.

**Acceptance Criteria** - Testable conditions that define "done."

## Writing Good Requirements

Requirements work best when they describe behavior concretely. Each requirement should answer:

1. What does it accept as input?
2. What does it return or produce?
3. What happens when something goes wrong?

You don't need formal requirement IDs like REQ-001. Just use descriptive headings. The LLM will figure out the mapping.

Be specific about edge cases. If your spec says "handle invalid input" without explaining what that means, you'll get the LLM's best guess. If you say "empty category string prints error and exits with code 1", you'll get exactly that.

If a spec gets too complex, plan generation can struggle with it. `ossature validate` warns you when this happens. Split the spec into smaller ones linked with `depends`. A monolithic "backend" spec works better as separate specs for auth, database, and API.

## Next Steps

- [AMD Format](amd.md) - Define architecture explicitly
- [Overview](overview.md) - How SMD and AMD work together
