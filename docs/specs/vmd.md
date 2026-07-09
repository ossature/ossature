# VMD Format

VMD (Verification Markdown) states the exact behavior you expect from generated code, as test cases you write yourself.

VMD is optional, like AMD. Without one, tests are ordinary tasks the LLM plans and writes itself, which means the same model writes both the code and the tests that grade it. With a VMD, you write the expected values, the LLM never sees them, and the build turns them into a real test suite that the code has to pass.

A VMD holds two constructs. A group is a table: one function, one example row per case. Use it for pure functions with many cases. A scenario is a named behavior written as given, when, then steps. Use it for anything that needs setup, a command run, or a sequence. Despite the family name, a VMD file is not Markdown; it's plain text.

## When to Use VMD

Write a VMD when a spec has behavior you can state as concrete values: a function that turns `"2h30m"` into `9000`, a command that must exit with code 1 on bad input, a flow where adding an expense makes it show up in the list. These are the cases you'd otherwise check by hand after every build.

Not everything fits. A check that compares against a golden file, or asserts a property over many inputs, belongs in a regular test task instead. See [What Belongs in a Test Task](#what-belongs-in-a-test-task) below.

## Structure

A VMD file starts with `@` directives, followed by groups and scenarios separated by blank lines. A group is a signature line, then one case per line. Lines starting with `#` are comments.

```
@spec EXPENSE_TRACKER

@fixture BASE = {"next_id": 4, "expenses": [{"id": 1, "date": "2026-03-01", "amount": "12.50", "category": "Food", "description": "Lunch at cafe"}]}

# Amounts are decimal strings, so the amount column is typed.

add_expense(data, amount:decimal, category, description, date) ~struct ~decimal
adds_expense | BASE | "3.00" | "Transport" | "Bus fare" | "2026-03-02" | Ok
zero_amount  | BASE | "0"    | "Food"      | ""         | "2026-03-02" | !ValueError: Amount must be positive

list_expenses(data, category)
by_category | BASE | "Food"      | [{"id": 1, "date": "2026-03-01", "amount": "12.50", "category": "Food", "description": "Lunch at cafe"}]
no_match    | BASE | "Utilities" | []

delete_expense(data, expense_id)
missing_id | BASE | 99 | !KeyError

scenario add then list round trip:
when $ spenny add --amount 3.00 --category Transport
then stdout has "Expense added"
when $ spenny list
then stdout has "3.00"
```

This mirrors the expense tracker from the [SMD](smd.md) and [AMD](amd.md) pages. The group names are the functions the AMD's Core component declares, the `!KeyError` case tests the same behavior the component's contract promises, and the scenario checks the flow end to end through the real command.

## Directives

These go at the top of the file, one per line.

| Directive | Required | Description |
|-----------|----------|-------------|
| `@spec ID` | Yes | The `id` of the SMD this file verifies. One `@spec` per file. |
| `@arch ID` | No | The spec whose AMD interfaces the groups bind to. Defaults to `@spec`. |
| `@status value` | No | `draft`, `review`, `approved`, `implemented`, or `deprecated`. Defaults to `draft`. |
| `@fixture NAME = value` | No | A named value reused across case rows and scenarios. |
| `@covers target` | No | Ties the next group or scenario to one or more SMD requirements. |

## Groups

A group starts with a signature line:

```
func(param1, param2) [-> return] [~mode ...]
```

The function name is the target. Validate warns when it doesn't appear in the paired AMD's interface blocks, so a typo surfaces before any build. The parameter names label the input columns and set how many input cells each row needs. The return annotation is documentation only.

Each case row is:

```
name | input1 | input2 | ... | expected
```

Case names are stable identifiers: inserting a case never renumbers anything, so existing build state stays valid. Every input cell is one JSON value, so types are explicit. `9000` is a number and `"9000"` is a string, and a `|` inside a string is safe. The bare tokens `NaN`, `Infinity`, and `-Infinity` work as float inputs.

The expected cell is one of:

- A JSON value, compared under the group's compare mode
- `!ErrorType` for a call that must raise that error type
- `!ErrorType: message` to also require the message to contain the given text
- `Ok` for a call that must succeed, without comparing the value

Be as specific in the error cases as in the happy path. `!ValueError: Amount must be positive` checks both the type and the message, the same way the SMD's `**Errors:**` bullets state condition and response.

## Fixtures

A value that repeats across rows gets a name once:

```
@fixture BASE = {"next_id": 4, "expenses": []}
```

A fixture name substitutes for a whole input cell. There's no interpolation inside a larger value.

An opaque fixture, written with a `!`, names a handle the harness constructs fresh for every case, like a database connection. A parameter with that name consumes no column:

```
@fixture conn = !fresh empty sqlite

add(conn, url, desc)
empty_url | "" | "x" | !InvalidInput: url must not be empty
```

Opaque fixtures must be stateless constructors. If a case needs a handle with data already in it, or state carried from a previous row, that's a sequence, and sequences belong in a regular test task.

## Compare Modes

Modes go on the signature line and combine freely.

| Mode | Meaning |
|------|---------|
| (none) | Exact equality |
| `~approx` or `~approx:0.001` | Numeric closeness, for float outputs |
| `~unordered` | Order-insensitive collection equality |
| `~matches` | The expected value is a regex searched against the string form of the return |
| `~struct` | Tuples and named tuples normalize before comparing, so a named tuple matches a JSON object |
| `~decimal` | Numbers and numeric strings compare as exact decimals, so `"12.50"` stays distinct from `"12.5"` |

A parameter can carry a `:decimal` type suffix. The harness then builds the language's exact-decimal type from the cell, preserving trailing zeros. The expense tracker stores amounts as decimal strings, so its `amount` column is typed and its group compares with `~decimal`.

## Scenarios

A scenario is one named behavior: given some state, when something runs, then something holds. Each step has a fixed form, so validate checks every line before any LLM runs.

```
scenario deleting a missing expense fails:
given data = {"next_id": 1, "expenses": []}
when delete_expense(data, 99)
then raises KeyError
```

The scenario name is plain words and doubles as the case identity, so pick names that state the behavior. `given` binds a named JSON value, or references an `@fixture` by name. `when` is either a function call, with arguments that are JSON literals or given names, or a command after a `$`. `then` asserts the outcome.

A function scenario has one `when` and one `then`: `then returns <JSON>`, `then raises Type: message` (the message is a contains check), or `then ok` for a call that must just succeed.

A command scenario can have several `when $` steps. The steps share a working directory, so a file the first step writes is there for the second. Each scenario gets a fresh directory, so no state carries over between scenarios.

```
scenario rejects invalid utf-8 argv:
when $ yep \xff\xfe
then exit 1
then stderr has "UTF-8"

scenario add then list round trip:
when $ spenny add --amount 12.50 --category Food
> Expense added: #1
when $ spenny list
then stdout has "12.50"
```

Command words split like a shell's: double quotes group words with spaces, and a `\xNN` escape produces a raw byte for arguments that aren't valid UTF-8. There's no shell beyond that; pipes and redirects are validation errors, because a step runs one program.

Each `when $` step asserts with:

- `then exit N` - the exit code. Omitted means exit 0.
- `> text` lines - expected stdout, verbatim, one line each. The whole output must match, tolerating one trailing newline.
- `then stdout has "text"` / `is` / `matches` / `empty` - a contains, exact, regex, or emptiness check, when verbatim lines are more than you need. Same for `stderr`.

## Requirement Coverage

Groups and scenarios map to SMD requirements the way AMDs map to specs, except the link is inferred where it can be. A group named `timeago`, or a scenario that calls `timeago`, covers a requirement titled `timeago` automatically. When names don't line up, or one case verifies several requirements, declare it with `@covers` above the signature or the scenario line:

```
@covers add-expense
add_expense(data, amount:decimal, category, description, date)
```

A target is a requirement anchor or the quoted heading text. Anchors are optional slugs on SMD requirement headings, and they survive renaming the heading:

```
### Add an Expense {#add-expense}
### Data Persistence {#persistence .no-verify}
```

`.no-verify` marks a requirement as intentionally unverified, so coverage reporting stops flagging it. Use it for requirements that aren't testable with concrete values, like rendering or audio behavior.

`ossature validate` prints a coverage table from all of this: which requirements are covered and by what, which declared errors have no `!Error` case, and which `@covers` targets don't resolve. Plan tasks count too. A task in `plan.toml` can declare `covers = ["add-expense"]`, so a golden-file or roundtrip test outside the VMD still closes coverage for its requirement. Uncovered requirements are warnings by default; set `require_coverage = true` under `[test]` to make validate fail on them.

## How Cases Run

VMD cases are read at every stage, like contracts. Validate parses every file and cross-checks it with no LLM calls: structure, group signatures, JSON cells, fixture references, `@covers` resolution, and the coverage table. The auditor reads the cases alongside the spec and flags any case that contradicts a requirement or an AMD contract. The planner is told which targets have author cases so it doesn't plan duplicate test tasks, and it never writes or edits verification tasks itself.

For each group, and for each file's scenarios as a bundle, `ossature audit` appends a verification task to the plan, after the implementation tasks of its spec. The task is deterministic. When it runs, Ossature serializes the cases to a fixture in `checks/`, generates a test harness from a fixed template, and runs the suite as the task's verify command. Command scenarios run each step in a fresh per-scenario working directory. No LLM output is anywhere in that path, and the implementer's prompt never includes the VMD, so the code can't be written to fit the tests.

When the suite fails, the same fix loop from the [build system](../advanced/build-system.md) runs, with one difference: the fixer is pointed at the implementation files, and the fixture and harness are read-only to it. The whole `checks/` directory rejects agent writes. The harness also asserts that it executed exactly as many cases as the fixture holds, so a loader that silently skips cases can't pass.

Invalidation works like any other task. Editing a case changes the verification task's input hash, so the next build re-runs the suite against the existing code without rebuilding it. Comment and alignment edits don't invalidate anything, because the fixture serialization is canonical.

Function groups and function scenarios need Python output for now; for other languages they still validate and count toward coverage, with a warning at audit time. Command scenarios work for every output language, since their harness just runs the built program. It looks in the common build locations (`target/release/`, `zig-out/bin/`, `build/`, and so on) and falls back to the command name on `PATH`. For non-Python projects the harness lives in `checks/` next to the fixture, out of the way of the language's own test directory, and `python` with `pytest` must be available where the build runs.

## The [test] Section

```toml
[test]
runner = "pytest"                       # test tool the harness targets
command = "uv run pytest {file} -q"     # override the verify command; {file} is the harness path
require_coverage = false                # fail validate on uncovered requirements
```

Without a `command`, verification tasks run `python -m pytest <harness> -q`.

## What Belongs in a Test Task

A VMD case is a literal input with a literal expected outcome. Some verification needs a different shape, and those stay ordinary plan tasks with verify commands:

- Golden binary files (codecs, file formats) - a verify command diffing against a committed golden file
- Roundtrip and property assertions like `decode(encode(x)) == x` - a test task with the property in code
- Streaming and interactive behavior - a verify command with a bounding consumer
- State that a scenario's working directory can't carry, like a database server or the network

Tag those tasks with `covers` so the coverage table counts what they verify.

## Next Steps

- [SMD Format](smd.md) - The spec format
- [AMD Format](amd.md) - Define architecture explicitly
- [Overview](overview.md) - How the formats work together
