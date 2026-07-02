import json
import re
from pathlib import Path
from typing import Any

from ossature.models.shared import Status
from ossature.models.vmd import CliCase, Fixture, Group, Param, ValueCase, VMDSpec


class VMDParseError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Invalid VMD spec ({len(errors)} error(s)):\n{summary}")


_SPEC_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CASE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_GROUP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_DECIMAL_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_FIXTURE_RE = re.compile(r"^@fixture\s+(\S+)\s*=\s*(.+)$")
_BYTES_TOKEN_RE = re.compile(r"!bytes\[([^\]]*)\]")
_COVERS_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Fixture names that would collide with literals the cell decoder must keep
# for itself.
_RESERVED_FIXTURE_NAMES = frozenset({"true", "false", "null", "NaN", "Infinity", "Ok"})

_VALUE_MODES = frozenset({"approx", "unordered", "matches", "struct", "decimal"})
_BLESSED_PARAM_TYPES = frozenset({"decimal"})

# Sentinel used to smuggle byte literals through json.loads on cli argv
# cells. Chosen to be a string no author would plausibly write as a literal
# argv element.
_BYTES_SENTINEL = "__ossature_bytes_{n}__"
_BYTES_SENTINEL_RE = re.compile(r"^__ossature_bytes_(\d+)__$")


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


def parse_vmd(text: str) -> VMDSpec:
    errors: list[str] = []
    warnings: list[str] = []
    raw_lines = text.split("\n")

    # Pass 1: collect fixtures so groups anywhere in the file can use them.
    fixtures: dict[str, Fixture] = {}
    for lineno, raw in enumerate(raw_lines, start=1):
        stripped = _strip_comment(raw).strip()
        if not stripped.startswith("@fixture"):
            continue
        _parse_fixture(stripped, lineno, fixtures, errors)

    # Pass 2: directives, group signatures, and case rows.
    spec_id = ""
    arch_id = ""
    status_value = ""
    directive_lines: dict[str, int] = {}
    groups: list[Group] = []
    seen_groups: set[tuple[str, int, str]] = set()
    pending_covers: list[str] = []
    pending_covers_line = 0
    current: Group | None = None
    invalid = Group(name="", kind="invalid")

    for lineno, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            current = None
            continue
        line = _strip_comment(raw).strip()
        if not line:
            # A comment-only line separates nothing; the group stays open.
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

        if current is None:
            group, sig_errors = _parse_signature(line, lineno, fixtures)
            errors.extend(sig_errors)
            if group is None:
                current = invalid
                continue
            group.covers = pending_covers
            pending_covers = []
            key = (group.name, group.arity, group.kind)
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
        if current.kind == "cli":
            _parse_cli_row(current, line, lineno, errors)
        else:
            _parse_value_row(current, line, lineno, fixtures, errors)

    if pending_covers:
        errors.append(f"line {pending_covers_line}: @covers is not followed by a group signature")

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

    if not groups:
        errors.append("No case groups found (need at least one)")
    for group in groups:
        empty = not group.cli_cases if group.kind == "cli" else not group.cases
        if empty:
            errors.append(f"Group '{group.name}': no case rows")

    if errors:
        raise VMDParseError(errors)

    return VMDSpec(
        spec_id=spec_id,
        arch_id=arch_id or spec_id,
        status=status,
        fixtures=sorted(fixtures.values(), key=lambda f: f.line),
        groups=groups,
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
            f"line {lineno}: expected a group signature like 'name(param1, param2)', got: {line!r}"
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
    is_cli = False
    for token in modes_text.split():
        if not token.startswith("~"):
            errors.append(f"line {lineno}: expected a ~mode token, got '{token}'")
            continue
        body = token[1:]
        mode, _, arg = body.partition(":")
        if mode == "cli":
            is_cli = True
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
    if is_cli and modes:
        errors.append(
            f"line {lineno}: ~cli cannot combine with other group modes "
            f"(use a per-cell '~matches' prefix instead)"
        )

    if is_cli:
        param_names = [p.strip() for p in params_text.split(",") if p.strip()]
        if param_names != ["argv"]:
            errors.append(f"line {lineno}: a ~cli group signature must be '{name}(argv) ~cli'")
        if returns:
            errors.append(f"line {lineno}: a ~cli group cannot declare a return")
        if errors:
            return None, errors
        return Group(name=name, kind="cli", line=lineno), errors

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


def _parse_bytes_token(body: str) -> bytes | None:
    if not body.strip():
        return None
    out = bytearray()
    for part in body.split(","):
        part = part.strip()
        try:
            n = int(part, 16) if part.lower().startswith("0x") else int(part)
        except ValueError:
            return None
        if not 0 <= n <= 255:
            return None
        out.append(n)
    return bytes(out)


def _decode_argv_cell(raw: str) -> tuple[list[Any] | None, str | None]:
    """Decode a cli argv cell: a JSON array of strings, where an element may
    be a '!bytes[...]' literal for a non-UTF-8 argument."""
    mask = _scan_outside_strings(raw)
    byte_values: list[bytes] = []

    def replace(m: re.Match[str]) -> str:
        if mask[m.start()]:
            return m.group(0)
        parsed = _parse_bytes_token(m.group(1))
        if parsed is None:
            byte_values.append(b"")
            return m.group(0)
        byte_values.append(parsed)
        return json.dumps(_BYTES_SENTINEL.format(n=len(byte_values) - 1))

    prepared = _BYTES_TOKEN_RE.sub(replace, raw)
    if any(v == b"" for v in byte_values):
        return None, "malformed !bytes[...] literal (comma-separated 0-255 values)"
    try:
        decoded = json.loads(prepared)
    except ValueError:
        return None, f"argv is not a valid JSON array: {raw!r}"
    if not isinstance(decoded, list):
        return None, "argv must be a JSON array"
    argv: list[Any] = []
    for item in decoded:
        if isinstance(item, str):
            if m := _BYTES_SENTINEL_RE.match(item):
                argv.append(byte_values[int(m.group(1))])
            else:
                argv.append(item)
        else:
            return None, "argv elements must be JSON strings"
    return argv, None


def _decode_stream_cell(raw: str) -> tuple[str | None, bool, str | None]:
    """Decode a cli stdout/stderr cell: empty (unchecked), a JSON string, or
    a '~matches' prefix and a JSON string pattern.

    Returns (value, is_pattern, error)."""
    if not raw:
        return None, False, None
    is_pattern = False
    text = raw
    if text.startswith("~matches"):
        is_pattern = True
        text = text[len("~matches") :].strip()
        if not text:
            return None, False, "~matches needs a string pattern"
    try:
        decoded = json.loads(text)
    except ValueError:
        return None, False, f"expected a JSON string, got: {raw!r}"
    if not isinstance(decoded, str):
        return None, False, f"expected a JSON string, got: {raw!r}"
    if is_pattern:
        try:
            re.compile(decoded)
        except re.error as e:
            return None, False, f"invalid ~matches pattern: {e}"
    return decoded, is_pattern, None


def _parse_cli_row(group: Group, line: str, lineno: int, errors: list[str]) -> None:
    cells = _split_cells(line)
    if not 2 <= len(cells) <= 5:
        errors.append(
            f"line {lineno}: cli group '{group.name}' rows need 2 to 5 columns "
            f"(name, argv, stdout, exit, stderr), got {len(cells)}"
        )
        return

    name = cells[0]
    if not _CASE_NAME_RE.match(name):
        errors.append(f"line {lineno}: invalid case name '{name}'")
        return
    if name in group.case_names:
        errors.append(f"line {lineno}: duplicate case name '{name}' in group '{group.name}'")
        return

    argv, err = _decode_argv_cell(cells[1])
    if err or argv is None:
        errors.append(f"line {lineno}: case '{name}': {err}")
        return

    case = CliCase(name=name, argv=argv, line=lineno)

    if len(cells) > 2:
        value, is_pattern, err = _decode_stream_cell(cells[2])
        if err:
            errors.append(f"line {lineno}: case '{name}', stdout: {err}")
            return
        case.stdout, case.stdout_is_pattern = value, is_pattern

    if len(cells) > 3 and cells[3]:
        try:
            exit_code = json.loads(cells[3])
        except ValueError:
            exit_code = None
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            errors.append(
                f"line {lineno}: case '{name}', exit: expected an integer, got: {cells[3]!r}"
            )
            return
        case.exit_code = exit_code

    if len(cells) > 4:
        value, is_pattern, err = _decode_stream_cell(cells[4])
        if err:
            errors.append(f"line {lineno}: case '{name}', stderr: {err}")
            return
        case.stderr, case.stderr_is_pattern = value, is_pattern

    if case.stdout is None and case.exit_code is None and case.stderr is None:
        errors.append(
            f"line {lineno}: case '{name}': at least one of stdout, exit, or stderr must be checked"
        )
        return

    group.cli_cases.append(case)
