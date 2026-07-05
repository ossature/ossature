import json
from pathlib import Path

from ossature.models.vmd import CliCase, Fixture, Group, ValueCase, VMDSpec


def _render_fixture(fixture: Fixture) -> str:
    if fixture.opaque:
        return f"@fixture {fixture.name} = !{fixture.label}"
    return f"@fixture {fixture.name} = {fixture.raw}"


def _render_signature(group: Group) -> str:
    if group.kind == "cli":
        return f"{group.name}(argv) ~cli"
    params = ", ".join(f"{p.name}:{p.type}" if p.type else p.name for p in group.params)
    sig = f"{group.name}({params})"
    if group.returns:
        sig += f" -> {group.returns}"
    for mode in group.compare_modes:
        if mode == "approx" and group.approx_tol is not None:
            sig += f" ~approx:{group.approx_tol}"
        else:
            sig += f" ~{mode}"
    return sig


def _render_expected(case: ValueCase) -> str:
    if case.expect_kind == "error":
        if case.error_message:
            return f"!{case.error_type}: {case.error_message}"
        return f"!{case.error_type}"
    if case.expect_kind == "ok":
        return "Ok"
    return case.raw_expected


def _render_value_case(case: ValueCase) -> str:
    cells = [case.name, *case.raw_inputs, _render_expected(case)]
    return " | ".join(cells)


def _render_stream(value: str | None, is_pattern: bool) -> str:
    if value is None:
        return ""
    rendered = json.dumps(value)
    return f"~matches {rendered}" if is_pattern else rendered


def _render_cli_case(case: CliCase) -> str:
    parts = []
    for item in case.argv:
        if isinstance(item, bytes):
            parts.append(f"!bytes[{','.join(f'0x{b:02x}' for b in item)}]")
        else:
            parts.append(json.dumps(item))
    argv = f"[{', '.join(parts)}]"
    cells = [
        case.name,
        argv,
        _render_stream(case.stdout, case.stdout_is_pattern),
        "" if case.exit_code is None else str(case.exit_code),
        _render_stream(case.stderr, case.stderr_is_pattern),
    ]
    # Trailing unchecked channels are dropped; interior ones keep their slot.
    while len(cells) > 2 and cells[-1] == "":
        cells.pop()
    return " | ".join(cells)


def render_group(group: Group) -> str:
    lines = []
    if group.covers:
        lines.append(f"@covers {', '.join(group.covers)}")
    lines.append(_render_signature(group))
    if group.kind == "cli":
        lines.extend(_render_cli_case(c) for c in group.cli_cases)
    else:
        lines.extend(_render_value_case(c) for c in group.cases)
    return "\n".join(lines)


def render_vmd(spec: VMDSpec) -> str:
    lines = [f"@spec {spec.spec_id}"]
    if spec.arch_id and spec.arch_id != spec.spec_id:
        lines.append(f"@arch {spec.arch_id}")
    lines.append(f"@status {spec.status.value}")
    blocks = ["\n".join(lines)]
    if spec.fixtures:
        blocks.append("\n".join(_render_fixture(f) for f in spec.fixtures))
    blocks.extend(render_group(g) for g in spec.groups)
    return "\n\n".join(blocks) + "\n"


def save_vmd(spec: VMDSpec, path: Path, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_vmd(spec), encoding="utf-8")

    return path
