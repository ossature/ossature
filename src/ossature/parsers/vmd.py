import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ossature.models.shared import Status
from ossature.models.vmd import (
    CallStep,
    CommandStep,
    Fixture,
    GivenBinding,
    Group,
    Param,
    Scenario,
    ValueCase,
    VMDSpec,
)


class VMDParseError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Invalid VMD spec ({len(errors)} error(s)):\n{summary}")


_SPEC_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENT_AT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CASE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_GROUP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_DECIMAL_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_FIXTURE_RE = re.compile(r"^@fixture\s+(\S+)\s*=\s*(.+)$")
_COVERS_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SCENARIO_RE = re.compile(r"^scenario\s+(.+):$")
_GIVEN_RE = re.compile(r"^given\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(.+))?$")
_CALL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)\((.*)\)$")
_STREAM_THEN_RE = re.compile(r"^(stdout|stderr)\s+(has|is|matches|empty)\s*(.*)$")
_HEX_RE = re.compile(r"[0-9a-fA-F]{2}")

# Fixture names that would collide with literals the cell decoder must keep
# for itself.
_RESERVED_FIXTURE_NAMES = frozenset({"true", "false", "null", "NaN", "Infinity", "Ok"})

_VALUE_MODES = frozenset({"approx", "unordered", "matches", "struct", "decimal"})
_BLESSED_PARAM_TYPES = frozenset({"decimal"})

# Words a shell would treat specially. A scenario command is an exec call,
# not a shell, so these are rejected outside quotes.
_SHELL_METACHARS = frozenset("|<>;&")

_STEP_HINT = "inside a scenario, lines must start with given, when, then, or >"


def _scan_outside_strings(line: str) -> list[bool]:
    """Return a per-character mask: True where the character sits inside a
    double-quoted JSON string (including the quotes themselves)."""
    mask = [False] * len(line)
    in_string = False
    escape = False
    for i, ch in enumerate(line):
        if in_string:
            mask[i] = True
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            mask[i] = True
    return mask


def _strip_comment(line: str) -> str:
    """Drop a trailing '# ...' comment, ignoring '#' inside JSON strings."""
    mask = _scan_outside_strings(line)
    for i, ch in enumerate(line):
        if ch == "#" and not mask[i]:
            return line[:i]
    return line


def _split_cells(line: str) -> list[str]:
    """Split a case row on '|' separators, ignoring '|' inside JSON strings."""
    mask = _scan_outside_strings(line)
    cells: list[str] = []
    start = 0
    for i, ch in enumerate(line):
        if ch == "|" and not mask[i]:
            cells.append(line[start:i].strip())
            start = i + 1
    cells.append(line[start:].strip())
    return cells


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass
class _ScenarioBuilder:
    """Parse state for the scenario stanza currently being read."""

    scenario: Scenario
    given_names: dict[str, GivenBinding] = field(default_factory=dict)
    exit_seen: bool = False
    dead: bool = False


def parse_vmd(text: str) -> VMDSpec:
    errors: list[str] = []
    warnings: list[str] = []
    raw_lines = text.split("\n")

    # Pass 1: collect fixtures so groups and scenarios anywhere in the file
    # can use them.
    fixtures: dict[str, Fixture] = {}
    for lineno, raw in enumerate(raw_lines, start=1):
        stripped = _strip_comment(raw).strip()
        if not stripped.startswith("@fixture"):
            continue
        _parse_fixture(stripped, lineno, fixtures, errors)

    # Pass 2: directives, group signatures, case rows, and scenario stanzas.
    spec_id = ""
    arch_id = ""
    status_value = ""
    directive_lines: dict[str, int] = {}
    groups: list[Group] = []
    scenarios: list[Scenario] = []
    seen_groups: set[tuple[str, int]] = set()
    seen_slugs: dict[str, int] = {}
    pending_covers: list[str] = []
    pending_covers_line = 0
    current: Group | _ScenarioBuilder | None = None
    invalid = Group(name="", kind="invalid")

    for lineno, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            current = None
            continue

        # Verbatim output lines keep their raw text: a '#' there is part of
        # the expected stdout, not a comment.
        if isinstance(current, _ScenarioBuilder) and raw.strip().startswith(">"):
            _parse_output_line(current, raw.strip(), lineno, errors)
            continue

        line = _strip_comment(raw).strip()
        if not line:
            # A comment-only line separates nothing; the stanza stays open.
            continue

        if line.startswith("@"):
            current = None
            word = line.split(None, 1)[0]
            rest = line[len(word) :].strip()
            if word == "@fixture":
                continue  # handled in pass 1
            if word == "@covers":
                targets, cov_errors = _parse_covers(rest, lineno)
                errors.extend(cov_errors)
                pending_covers.extend(targets)
                pending_covers_line = lineno
                continue
            if word in ("@spec", "@arch", "@status"):
                if word in directive_lines:
                    errors.append(
                        f"line {lineno}: duplicate {word} directive "
                        f"(first on line {directive_lines[word]})"
                    )
                    continue
                directive_lines[word] = lineno
                if not rest:
                    errors.append(f"line {lineno}: {word} needs a value")
                elif word == "@spec":
                    if _SPEC_ID_RE.match(rest):
                        spec_id = rest
                    else:
                        errors.append(f"line {lineno}: invalid @spec id: '{rest}'")
                elif word == "@arch":
                    if _SPEC_ID_RE.match(rest):
                        arch_id = rest
                    else:
                        errors.append(f"line {lineno}: invalid @arch id: '{rest}'")
                else:
                    status_value = rest
                continue
            errors.append(f"line {lineno}: unknown directive '{word}'")
            continue

        if m := _SCENARIO_RE.match(line):
            name = m.group(1).strip()
            slug = _slugify(name)
            builder = _ScenarioBuilder(
                Scenario(name=name, slug=slug, covers=pending_covers, line=lineno)
            )
            pending_covers = []
            if not slug:
                errors.append(f"line {lineno}: scenario name needs at least one word")
                builder.dead = True
            elif slug in seen_slugs:
                errors.append(
                    f"line {lineno}: scenario '{name}' duplicates the scenario "
                    f"on line {seen_slugs[slug]}"
                )
                builder.dead = True
            else:
                seen_slugs[slug] = lineno
                scenarios.append(builder.scenario)
            current = builder
            continue

        if isinstance(current, _ScenarioBuilder):
            _parse_scenario_step(current, line, lineno, fixtures, errors)
            continue

        if current is None:
            group, sig_errors = _parse_signature(line, lineno, fixtures)
            errors.extend(sig_errors)
            if group is None:
                current = invalid
                continue
            group.covers = pending_covers
            pending_covers = []
            key = (group.name, group.arity)
            if key in seen_groups:
                errors.append(
                    f"line {lineno}: duplicate group '{group.name}' with {group.arity} parameter(s)"
                )
            seen_groups.add(key)
            groups.append(group)
            current = group
            continue

        if current.kind == "invalid":
            continue
        _parse_value_row(current, line, lineno, fixtures, errors)

    if pending_covers:
        errors.append(f"line {pending_covers_line}: @covers is not followed by a group or scenario")

    if not spec_id and "@spec" not in directive_lines:
        errors.append("Missing required directive: @spec")

    status = Status.DRAFT
    if status_value:
        status_values = {e.value for e in Status}
        if status_value in status_values:
            status = Status(status_value)
        else:
            errors.append(
                f"Invalid status: '{status_value}'. "
                f"Expected one of: {', '.join(sorted(status_values))}"
            )

    if not groups and not scenarios:
        errors.append("No case groups or scenarios found (need at least one)")
    for group in groups:
        if not group.cases:
            errors.append(f"Group '{group.name}': no case rows")
    for scenario in scenarios:
        if not scenario.kind:
            errors.append(f"Scenario '{scenario.name}': no when step")
        elif (
            scenario.kind == "call" and scenario.call is not None and not scenario.call.expect_kind
        ):
            errors.append(f"Scenario '{scenario.name}': the when call has no then")

    if errors:
        raise VMDParseError(errors)

    return VMDSpec(
        spec_id=spec_id,
        arch_id=arch_id or spec_id,
        status=status,
        fixtures=sorted(fixtures.values(), key=lambda f: f.line),
        groups=groups,
        scenarios=scenarios,
        warnings=warnings,
    )


def parse_vmd_file(path: str | Path) -> VMDSpec:
    return parse_vmd(Path(path).read_text())


def _parse_fixture(line: str, lineno: int, fixtures: dict[str, Fixture], errors: list[str]) -> None:
    m = _FIXTURE_RE.match(line)
    if not m:
        errors.append(f"line {lineno}: malformed @fixture (expected '@fixture NAME = value')")
        return
    name, rest = m.group(1), m.group(2).strip()
    if not _IDENT_RE.match(name):
        errors.append(f"line {lineno}: invalid fixture name '{name}'")
        return
    if name in _RESERVED_FIXTURE_NAMES:
        errors.append(f"line {lineno}: fixture name '{name}' is reserved")
        return
    if name in fixtures:
        errors.append(
            f"line {lineno}: duplicate fixture '{name}' (first on line {fixtures[name].line})"
        )
        return
    if rest.startswith("!"):
        label = rest[1:].strip()
        if not label:
            errors.append(f"line {lineno}: opaque fixture '{name}' needs a constructor label")
            return
        fixtures[name] = Fixture(name=name, opaque=True, label=label, line=lineno)
        return
    try:
        value = json.loads(rest)
    except ValueError:
        errors.append(f"line {lineno}: fixture '{name}' value is not valid JSON: {rest!r}")
        return
    fixtures[name] = Fixture(name=name, value=value, raw=rest, line=lineno)


def _parse_covers(rest: str, lineno: int) -> tuple[list[str], list[str]]:
    if not rest:
        return [], [f"line {lineno}: @covers needs at least one target"]
    targets: list[str] = []
    errors: list[str] = []
    mask = _scan_outside_strings(rest)
    parts: list[str] = []
    start = 0
    for i, ch in enumerate(rest):
        if ch == "," and not mask[i]:
            parts.append(rest[start:i].strip())
            start = i + 1
    parts.append(rest[start:].strip())
    for part in parts:
        if not part:
            errors.append(f"line {lineno}: empty @covers target")
        elif part.startswith('"'):
            try:
                decoded = json.loads(part)
            except ValueError:
                errors.append(f"line {lineno}: malformed quoted @covers target: {part}")
                continue
            if not isinstance(decoded, str) or not decoded.strip():
                errors.append(f"line {lineno}: @covers target must be a non-empty string")
                continue
            targets.append(decoded.strip())
        elif _COVERS_SLUG_RE.match(part):
            targets.append(part)
        else:
            errors.append(
                f"line {lineno}: invalid @covers target '{part}' "
                f"(use an anchor slug or a quoted heading)"
            )
    return targets, errors


def _parse_signature(
    line: str, lineno: int, fixtures: dict[str, Fixture]
) -> tuple[Group | None, list[str]]:
    errors: list[str] = []
    open_idx = line.find("(")
    close_idx = line.find(")", open_idx) if open_idx != -1 else -1
    if open_idx <= 0 or close_idx == -1:
        return None, [
            f"line {lineno}: expected a group signature like 'name(param1, param2)' "
            f"or a 'scenario name:' stanza, got: {line!r}"
        ]
    name = line[:open_idx].strip()
    if not _GROUP_NAME_RE.match(name):
        errors.append(f"line {lineno}: invalid group name '{name}'")
    params_text = line[open_idx + 1 : close_idx]
    rest = line[close_idx + 1 :].strip()

    returns = ""
    modes_text = rest
    if rest.startswith("->"):
        tilde = rest.find("~")
        returns = rest[2 : tilde if tilde != -1 else len(rest)].strip()
        modes_text = rest[tilde:] if tilde != -1 else ""
    elif rest and not rest.startswith("~"):
        errors.append(f"line {lineno}: unexpected text after signature: {rest!r}")
        modes_text = ""

    modes: list[str] = []
    approx_tol: float | None = None
    for token in modes_text.split():
        if not token.startswith("~"):
            errors.append(f"line {lineno}: expected a ~mode token, got '{token}'")
            continue
        body = token[1:]
        mode, _, arg = body.partition(":")
        if mode == "cli":
            errors.append(
                f"line {lineno}: ~cli groups were replaced by scenarios; "
                f"write a 'scenario name:' stanza with 'when $ {name} ...'"
            )
        elif mode in _VALUE_MODES:
            if mode == "approx" and arg:
                try:
                    approx_tol = float(arg)
                except ValueError:
                    errors.append(f"line {lineno}: invalid ~approx tolerance '{arg}'")
            elif arg:
                errors.append(f"line {lineno}: mode ~{mode} takes no argument")
            if mode in modes:
                errors.append(f"line {lineno}: duplicate mode ~{mode}")
            else:
                modes.append(mode)
        else:
            errors.append(f"line {lineno}: unknown mode ~{mode}")

    params: list[Param] = []
    seen_params: set[str] = set()
    for part in params_text.split(","):
        part = part.strip()
        if not part:
            if params_text.strip():
                errors.append(f"line {lineno}: empty parameter in signature")
            continue
        pname, _, ptype = part.partition(":")
        pname, ptype = pname.strip(), ptype.strip()
        if not _IDENT_RE.match(pname):
            errors.append(f"line {lineno}: invalid parameter name '{pname}'")
            continue
        if ptype and ptype not in _BLESSED_PARAM_TYPES:
            errors.append(
                f"line {lineno}: unknown parameter type '{ptype}' "
                f"(supported: {', '.join(sorted(_BLESSED_PARAM_TYPES))})"
            )
            ptype = ""
        if pname in seen_params:
            errors.append(f"line {lineno}: duplicate parameter name '{pname}'")
            continue
        seen_params.add(pname)
        opaque = ""
        fixture = fixtures.get(pname)
        if fixture is not None and fixture.opaque:
            opaque = pname
        params.append(Param(name=pname, type=ptype, opaque_fixture=opaque))

    if errors:
        return None, errors
    return Group(
        name=name,
        kind="value",
        params=params,
        returns=returns,
        compare_modes=modes,
        approx_tol=approx_tol,
        line=lineno,
    ), errors


def _decode_value_cell(raw: str, fixtures: dict[str, Fixture]) -> tuple[Any, str, str | None]:
    """Decode one input/expected cell. Returns (value, raw_text, error)."""
    if not raw:
        return None, raw, "empty cell"
    if _IDENT_RE.match(raw) and raw in fixtures:
        fixture = fixtures[raw]
        if fixture.opaque:
            return None, raw, f"opaque fixture '{raw}' cannot be used as a value"
        return fixture.value, fixture.raw, None
    try:
        return json.loads(raw), raw, None
    except ValueError:
        return None, raw, f"not valid JSON (and not a known fixture name): {raw!r}"


def _parse_value_row(
    group: Group,
    line: str,
    lineno: int,
    fixtures: dict[str, Fixture],
    errors: list[str],
) -> None:
    cells = _split_cells(line)
    columns = [p for p in group.params if not p.opaque_fixture]
    expected_count = 1 + len(columns) + 1
    if len(cells) != expected_count:
        errors.append(
            f"line {lineno}: group '{group.name}' rows need {expected_count} columns "
            f"(name, {len(columns)} input(s), expected), got {len(cells)}"
        )
        return

    name = cells[0]
    if not _CASE_NAME_RE.match(name):
        errors.append(f"line {lineno}: invalid case name '{name}'")
        return
    if name in group.case_names:
        errors.append(f"line {lineno}: duplicate case name '{name}' in group '{group.name}'")
        return

    inputs: list[Any] = []
    raw_inputs: list[str] = []
    ok = True
    for param, cell in zip(columns, cells[1:-1], strict=True):
        value, raw, err = _decode_value_cell(cell, fixtures)
        if err:
            errors.append(f"line {lineno}: case '{name}', input '{param.name}': {err}")
            ok = False
            continue
        if param.type == "decimal" and not _is_decimal_cell(value):
            errors.append(
                f"line {lineno}: case '{name}', input '{param.name}' is a decimal "
                f'column and needs a number or a numeric string like "12.50"'
            )
            ok = False
            continue
        inputs.append(value)
        raw_inputs.append(raw)

    case = ValueCase(name=name, inputs=inputs, raw_inputs=raw_inputs, line=lineno)
    expected_cell = cells[-1]
    if expected_cell.startswith("!"):
        etype, _, message = expected_cell[1:].partition(":")
        etype, message = etype.strip(), message.strip()
        if not _ERROR_TYPE_RE.match(etype):
            errors.append(f"line {lineno}: case '{name}': invalid error type '{etype}'")
            return
        case.expect_kind = "error"
        case.error_type = etype
        case.error_message = message
    elif expected_cell == "Ok":
        case.expect_kind = "ok"
    else:
        value, raw, err = _decode_value_cell(expected_cell, fixtures)
        if err:
            errors.append(f"line {lineno}: case '{name}', expected: {err}")
            return
        if "matches" in group.compare_modes and not isinstance(value, str):
            errors.append(
                f"line {lineno}: case '{name}': ~matches groups need a string "
                f"pattern as the expected value"
            )
            return
        case.expected = value
        case.raw_expected = raw

    if ok:
        group.cases.append(case)


def _is_decimal_cell(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    return isinstance(value, str) and bool(_DECIMAL_RE.match(value))


# Scenario steps


def _split_command_words(text: str) -> tuple[list[Any] | None, str | None]:
    """Split a `when $` command line into words.

    Whitespace separates words; double quotes group; a \\xNN escape inside a
    word produces a raw byte and makes the whole word a bytes argument. A
    scenario command is an exec call, so shell metacharacters outside quotes
    are rejected.
    """
    words: list[Any] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        buf = bytearray()
        has_bytes = False
        while i < n and text[i] not in " \t":
            ch = text[i]
            if ch == '"':
                i += 1
                closed = False
                while i < n:
                    qc = text[i]
                    if qc == '"':
                        closed = True
                        i += 1
                        break
                    if qc == "\\" and i + 1 < n:
                        nxt = text[i + 1]
                        if nxt == "x" and i + 3 < n and _HEX_RE.fullmatch(text[i + 2 : i + 4]):
                            buf.append(int(text[i + 2 : i + 4], 16))
                            has_bytes = True
                            i += 4
                            continue
                        if nxt in ('"', "\\"):
                            buf += nxt.encode()
                            i += 2
                            continue
                    buf += qc.encode()
                    i += 1
                if not closed:
                    return None, "unterminated quote in command"
            elif ch == "\\":
                if i + 3 < n and text[i + 1] == "x" and _HEX_RE.fullmatch(text[i + 2 : i + 4]):
                    buf.append(int(text[i + 2 : i + 4], 16))
                    has_bytes = True
                    i += 4
                elif i + 1 < n and text[i + 1] in ('"', "\\", " "):
                    buf += text[i + 1].encode()
                    i += 2
                else:
                    return None, f"invalid escape at {text[i : i + 4]!r} (use \\xNN for bytes)"
            elif ch in _SHELL_METACHARS:
                return None, (
                    f"shell features are not available ({ch!r}); a scenario "
                    f"runs one program per when step, no pipes or redirects"
                )
            else:
                buf += ch.encode()
                i += 1
        words.append(bytes(buf) if has_bytes else buf.decode("utf-8"))
    if not words:
        return None, "the command is empty"
    return words, None


def _parse_call_args(
    text: str,
    fixtures: dict[str, Fixture],
    givens: dict[str, GivenBinding],
) -> tuple[list[Any], list[str], str | None]:
    """Parse `when target(...)` arguments: JSON literals, given names, or
    value fixture names, comma separated."""
    args: list[Any] = []
    raws: list[str] = []
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        m = _IDENT_AT_RE.match(text, i)
        is_bare = False
        name = ""
        if m:
            j = m.end()
            while j < n and text[j] in " \t":
                j += 1
            is_bare = j >= n or text[j] == ","
            name = m.group()
        if is_bare and name in givens:
            binding = givens[name]
            args.append(binding.value)
            raws.append(binding.raw or name)
            i = m.end()  # type: ignore[union-attr]
        elif is_bare and name in fixtures and not fixtures[name].opaque:
            args.append(fixtures[name].value)
            raws.append(fixtures[name].raw)
            i = m.end()  # type: ignore[union-attr]
        elif is_bare and name in fixtures:
            return [], [], f"opaque fixture '{name}' cannot be used as a call argument"
        elif is_bare and name not in ("true", "false", "null", "NaN", "Infinity"):
            return [], [], f"unknown name '{name}' (not a given binding or fixture)"
        else:
            try:
                value, end = decoder.raw_decode(text, i)
            except ValueError:
                return [], [], f"argument is not valid JSON: {text[i:]!r}"
            args.append(value)
            raws.append(text[i:end])
            i = end
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        if text[i] != ",":
            return [], [], f"expected ',' between arguments, got {text[i:]!r}"
        i += 1
    return args, raws, None


def _parse_output_line(builder: _ScenarioBuilder, raw: str, lineno: int, errors: list[str]) -> None:
    scenario = builder.scenario
    if builder.dead:
        return
    if scenario.kind != "command" or not scenario.steps:
        errors.append(f"line {lineno}: '>' output lines need a preceding 'when $' step")
        builder.dead = True
        return
    step = scenario.steps[-1]
    if step.stdout_mode:
        errors.append(
            f"line {lineno}: scenario '{scenario.name}': '>' lines and a "
            f"'then stdout' check are mutually exclusive"
        )
        builder.dead = True
        return
    content = raw[1:]
    if content.startswith(" "):
        content = content[1:]
    if step.stdout_lines is None:
        step.stdout_lines = []
    step.stdout_lines.append(content)


def _parse_scenario_step(
    builder: _ScenarioBuilder,
    line: str,
    lineno: int,
    fixtures: dict[str, Fixture],
    errors: list[str],
) -> None:
    if builder.dead:
        return
    scenario = builder.scenario
    word = line.split(None, 1)[0]

    if word == "given":
        if scenario.kind:
            errors.append(f"line {lineno}: given steps come before the first when")
            builder.dead = True
            return
        m = _GIVEN_RE.match(line)
        if not m:
            errors.append(f"line {lineno}: expected 'given name = <JSON>' or 'given FIXTURE'")
            builder.dead = True
            return
        name, value_text = m.group(1), m.group(2)
        if name in builder.given_names:
            errors.append(f"line {lineno}: duplicate given '{name}'")
            builder.dead = True
            return
        if value_text is None:
            fixture = fixtures.get(name)
            if fixture is None:
                errors.append(f"line {lineno}: given references unknown fixture '{name}'")
                builder.dead = True
                return
            binding = GivenBinding(
                name=name,
                value=fixture.value,
                raw=fixture.raw,
                fixture=name,
                opaque=fixture.opaque,
                line=lineno,
            )
        else:
            try:
                value = json.loads(value_text)
            except ValueError:
                errors.append(
                    f"line {lineno}: given '{name}' value is not valid JSON: {value_text!r}"
                )
                builder.dead = True
                return
            binding = GivenBinding(name=name, value=value, raw=value_text.strip(), line=lineno)
        builder.given_names[name] = binding
        scenario.givens.append(binding)
        return

    if word == "when":
        rest = line[len("when") :].strip()
        if rest.startswith("$"):
            if scenario.kind == "call":
                errors.append(
                    f"line {lineno}: a scenario mixes call and command steps; use two scenarios"
                )
                builder.dead = True
                return
            words, err = _split_command_words(rest[1:].strip())
            if err or words is None:
                errors.append(f"line {lineno}: {err}")
                builder.dead = True
                return
            scenario.kind = "command"
            scenario.steps.append(CommandStep(argv=words))
            builder.exit_seen = False
            return
        m = _CALL_RE.match(rest)
        if not m:
            errors.append(f"line {lineno}: expected 'when target(args)' or 'when $ command'")
            builder.dead = True
            return
        if scenario.kind == "command":
            errors.append(
                f"line {lineno}: a scenario mixes call and command steps; use two scenarios"
            )
            builder.dead = True
            return
        if scenario.kind == "call":
            errors.append(
                f"line {lineno}: a function scenario has exactly one when step; "
                f"sequences are for command scenarios"
            )
            builder.dead = True
            return
        args, raws, err = _parse_call_args(m.group(2), fixtures, builder.given_names)
        if err:
            errors.append(f"line {lineno}: {err}")
            builder.dead = True
            return
        scenario.kind = "call"
        scenario.call = CallStep(target=m.group(1), args=args, raw_args=raws, expect_kind="")
        return

    if word == "then":
        rest = line[len("then") :].strip()
        if not scenario.kind:
            errors.append(f"line {lineno}: then needs a preceding when step")
            builder.dead = True
            return
        if scenario.kind == "call":
            _parse_call_then(builder, rest, lineno, errors)
        else:
            _parse_command_then(builder, rest, lineno, errors)
        return

    errors.append(f"line {lineno}: unexpected line {line!r} ({_STEP_HINT})")
    builder.dead = True


def _parse_call_then(builder: _ScenarioBuilder, rest: str, lineno: int, errors: list[str]) -> None:
    scenario = builder.scenario
    call = scenario.call
    if call is None:
        return
    if call.expect_kind:
        errors.append(
            f"line {lineno}: scenario '{scenario.name}': a function scenario has one then"
        )
        builder.dead = True
        return
    if rest == "ok":
        call.expect_kind = "ok"
    elif rest.startswith("returns"):
        value_text = rest[len("returns") :].strip()
        try:
            call.expected = json.loads(value_text)
        except ValueError:
            errors.append(f"line {lineno}: 'then returns' needs a JSON value, got: {value_text!r}")
            builder.dead = True
            return
        call.expect_kind = "value"
        call.raw_expected = value_text
    elif rest.startswith("raises"):
        body = rest[len("raises") :].strip()
        etype, _, message = body.partition(":")
        etype, message = etype.strip(), message.strip()
        if not _ERROR_TYPE_RE.match(etype):
            errors.append(f"line {lineno}: invalid error type '{etype}'")
            builder.dead = True
            return
        call.expect_kind = "error"
        call.error_type = etype
        call.error_message = message
    else:
        errors.append(
            f"line {lineno}: expected 'then returns <JSON>', 'then raises Type: message', "
            f"or 'then ok', got: {rest!r}"
        )
        builder.dead = True


def _parse_command_then(
    builder: _ScenarioBuilder, rest: str, lineno: int, errors: list[str]
) -> None:
    scenario = builder.scenario
    step = scenario.steps[-1]

    if rest.startswith("exit"):
        code_text = rest[len("exit") :].strip()
        if builder.exit_seen:
            errors.append(f"line {lineno}: duplicate 'then exit' for this when step")
            builder.dead = True
            return
        try:
            step.exit_code = int(code_text)
        except ValueError:
            errors.append(f"line {lineno}: 'then exit' needs an integer, got: {code_text!r}")
            builder.dead = True
            return
        builder.exit_seen = True
        return

    m = _STREAM_THEN_RE.match(rest)
    if not m:
        errors.append(
            f"line {lineno}: expected 'then exit N', "
            f"'then stdout|stderr has|is|matches \"text\"', or "
            f"'then stdout|stderr empty', got: {rest!r}"
        )
        builder.dead = True
        return
    channel, mode, value_text = m.group(1), m.group(2), m.group(3).strip()

    if channel == "stdout" and step.stdout_lines is not None:
        errors.append(
            f"line {lineno}: scenario '{scenario.name}': '>' lines and a "
            f"'then stdout' check are mutually exclusive"
        )
        builder.dead = True
        return
    already = step.stdout_mode if channel == "stdout" else step.stderr_mode
    if already:
        errors.append(f"line {lineno}: duplicate 'then {channel}' check")
        builder.dead = True
        return

    text: str | None = None
    if mode == "empty":
        if value_text:
            errors.append(f"line {lineno}: 'then {channel} empty' takes no value")
            builder.dead = True
            return
    else:
        try:
            decoded = json.loads(value_text)
        except ValueError:
            decoded = None
        if not isinstance(decoded, str):
            errors.append(
                f"line {lineno}: 'then {channel} {mode}' needs a JSON string, got: {value_text!r}"
            )
            builder.dead = True
            return
        if mode == "matches":
            try:
                re.compile(decoded)
            except re.error as e:
                errors.append(f"line {lineno}: invalid matches pattern: {e}")
                builder.dead = True
                return
        text = decoded

    if channel == "stdout":
        step.stdout = text
        step.stdout_mode = mode
    else:
        step.stderr = text
        step.stderr_mode = mode
