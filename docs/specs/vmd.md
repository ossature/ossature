# VMD Format

VMD (Verification Markdown) pins down the exact behavior you expect from generated code, as test cases you write yourself. Each case is one line: a name, the inputs, and the output or error you expect.

VMD is optional, like AMD. Without one, tests are ordinary tasks the LLM plans and writes itself, which means the same model writes both the code and the tests that grade it. A VMD breaks that loop: you write the expected values, the LLM never sees them, and the build turns them into a real test suite that the code has to pass.

Despite the family name, a VMD file is not Markdown. It's a plain-text table, one case per line, because a test corpus reads better as a table than as prose.

## When to Use VMD

Write a VMD when a spec has behavior you can pin to concrete values: a function that turns `"2h30m"` into `9000`, a command that must exit with code 1 on bad input. These are the cases you'd otherwise check by hand after every build.

Not everything fits. Behavior whose oracle is a file, a property, or a sequence of stateful steps belongs in a regular test task instead. See [What Belongs in a Test Task](#what-belongs-in-a-test-task) below.

## Structure

A VMD file starts with `@` directives, followed by groups separated by blank lines. Each group is one function or command under test: a signature line, then one case per line. Lines starting with `#` are comments.

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
```

This mirrors the expense tracker from the [SMD](smd.md) and [AMD](amd.md) pages. The group names are the functions the AMD's Core component declares, and the `!KeyError` case pins the same behavior the component's contract promises.

## Directives

These go at the top of the file, one per line.

| Directive | Required | Description |
|-----------|----------|-------------|
| `@spec ID` | Yes | The `id` of the SMD this file verifies. One `@spec` per file. |
| `@arch ID` | No | The spec whose AMD interfaces the groups bind to. Defaults to `@spec`. |
| `@status value` | No | `draft`, `review`, `approved`, `implemented`, or `deprecated`. Defaults to `draft`. |
| `@fixture NAME = value` | No | A named value reused across case rows. |
| `@covers target` | No | Ties the next group to one or more SMD requirements. |

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

Case names are yours to assign and they're stable: inserting a case never renumbers anything, so build state stays put. Every input cell is one JSON value, which keeps types honest. `9000` is a number and `"9000"` is a string, and a `|` inside a string is safe. The bare tokens `NaN`, `Infinity`, and `-Infinity` work as float inputs.

The expected cell is one of:

- A JSON value, compared under the group's compare mode
- `!ErrorType` for a call that must raise that error type
- `!ErrorType: message` to also require the message to contain the given text
- `Ok` for a call that must succeed, without comparing the value

Be as specific in the error cases as in the happy path. `!ValueError: Amount must be positive` pins both the type and the message, the same way the SMD's `**Errors:**` bullets pin condition and response.

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

## Command Groups

A `~cli` group tests a command instead of a function. The signature is always `program(argv) ~cli` and the columns are name, argv, stdout, exit code, stderr. Trailing columns are optional, and an empty cell means that channel isn't checked.

```
spenny(argv) ~cli
add_negative | ["add", "--amount", "-12.50", "--category", "Food"] | | 1 | ~matches "Amount must be positive"
list_empty   | ["list"] | "No expenses found." | 0
```

stdout and stderr cells are JSON strings compared exactly, tolerating one trailing newline, or patterns with a `~matches` prefix. An argv element can be a `!bytes[0x80,0xff]` literal for a non-UTF-8 argument. Each row must check at least one channel.

A `~cli` case is a single invocation that terminates on its own. There are no pipelines, no assertions on files the command writes, and no state between rows. A command that streams forever or writes its result to disk belongs in a regular test task.

## Requirement Coverage

Groups map to SMD requirements the way AMDs map to specs, except the link is inferred where it can be. A group named `timeago` covers a requirement titled `timeago` automatically. When names don't line up, or one group verifies several requirements, declare it with `@covers` above the signature:

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

For each group, `ossature audit` appends a verification task to the plan, after the implementation tasks of its spec. The task is deterministic. When it runs, Ossature serializes the cases to a fixture in `checks/`, generates a test harness from a fixed template, and runs the suite as the task's verify command. No LLM output is anywhere in that path, and the implementer's prompt never includes the VMD, so the code can't be written to fit the tests.

When the suite fails, the same fix loop from the [build system](../advanced/build-system.md) runs, with one difference: the fixer is pointed at the implementation files, and the fixture and harness are read-only to it. The whole `checks/` directory rejects agent writes. The harness also asserts that it executed exactly as many cases as the fixture holds, so a loader that silently skips cases can't pass.

Invalidation works like any other task. Editing a case changes the verification task's input hash, so the next build re-runs the suite against the existing code without rebuilding it. Comment and alignment edits don't invalidate anything, because the fixture serialization is canonical.

Function groups need Python output for now; for other languages they still validate and count toward coverage, with a warning at audit time. Command groups work for every output language, since their harness just runs the built program. It looks in the common build locations (`target/release/`, `zig-out/bin/`, `build/`, and so on) and falls back to the command name on `PATH`. For non-Python projects the harness lives in `checks/` next to the fixture, out of the way of the language's own test directory, and `python` with `pytest` must be available where the build runs.

## The [test] Section

```toml
[test]
runner = "pytest"                       # test tool the harness targets
command = "uv run pytest {file} -q"     # override the verify command; {file} is the harness path
require_coverage = false                # fail validate on uncovered requirements
```

Without a `command`, verification tasks run `python -m pytest <harness> -q`.

## What Belongs in a Test Task

A VMD case is a literal input with a literal expected value. Some verification needs a different shape, and those stay ordinary plan tasks with verify commands:

- Golden binary files (codecs, file formats) - a verify command diffing against a committed golden file
- Roundtrip and property assertions like `decode(encode(x)) == x` - a test task with the property in code
- Stateful sequences (add, then list, then delete) - a test task that scripts the sequence
- Streaming and interactive behavior - a verify command with a bounding consumer

Tag those tasks with `covers` so the coverage table stays honest about what they verify.

## Next Steps

- [SMD Format](smd.md) - The spec format
- [AMD Format](amd.md) - Define architecture explicitly
- [Overview](overview.md) - How the formats work together
