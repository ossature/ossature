import json
import re
from pathlib import Path

from ossature.models.vmd import (
    CommandStep,
    Fixture,
    Group,
    Scenario,
    ValueCase,
    VMDSpec,
)

# '#' must be in here: a bare word containing it would lose its tail to the
# parser's comment stripping on reparse
_WORD_SPECIALS = set(' \t"\\|<>;&#')

# Must accept exactly what the parser's _COVERS_SLUG_RE accepts; anything
# else renders as a quoted string
_COVERS_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _render_covers_target(target: str) -> str:
    if _COVERS_SLUG_RE.match(target):
        return target
    return json.dumps(target)


def _render_fixture(fixture: Fixture) -> str:
    if fixture.opaque:
        return f"@fixture {fixture.name} = !{fixture.label}"
    return f"@fixture {fixture.name} = {fixture.raw}"


def _render_signature(group: Group) -> str:
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


def render_group(group: Group) -> str:
    lines = []
    if group.covers:
        lines.append(f"@covers {', '.join(_render_covers_target(t) for t in group.covers)}")
    lines.append(_render_signature(group))
    lines.extend(_render_value_case(c) for c in group.cases)
    return "\n".join(lines)


def _render_command_word(word: str | bytes) -> str:
    if isinstance(word, bytes):
        # At least one byte must stay escaped, or the word would reparse as
        # a plain string.
        parts: list[str] = []
        escaped_any = False
        for b in word:
            ch = chr(b)
            if 0x20 < b < 0x7F and ch not in _WORD_SPECIALS:
                parts.append(ch)
            else:
                parts.append(f"\\x{b:02x}")
                escaped_any = True
        if not escaped_any and parts:
            parts[0] = f"\\x{word[0]:02x}"
        return "".join(parts)
    if word == "" or any(ch in _WORD_SPECIALS for ch in word):
        return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return word


def _render_stream_then(channel: str, mode: str, value: str | None) -> str:
    if mode == "empty":
        return f"then {channel} empty"
    return f"then {channel} {mode} {json.dumps(value)}"


def _render_command_step(step: CommandStep) -> list[str]:
    lines = [f"when $ {' '.join(_render_command_word(w) for w in step.argv)}"]
    if step.stdout_lines is not None:
        lines.extend(f"> {line}" if line else ">" for line in step.stdout_lines)
    if step.exit_code != 0:
        lines.append(f"then exit {step.exit_code}")
    if step.stdout_mode:
        lines.append(_render_stream_then("stdout", step.stdout_mode, step.stdout))
    if step.stderr_mode:
        lines.append(_render_stream_then("stderr", step.stderr_mode, step.stderr))
    return lines


def render_scenario(scenario: Scenario) -> str:
    lines = []
    if scenario.covers:
        lines.append(f"@covers {', '.join(_render_covers_target(t) for t in scenario.covers)}")
    lines.append(f"scenario {scenario.name}:")
    for given in scenario.givens:
        if given.fixture:
            lines.append(f"given {given.name}")
        else:
            lines.append(f"given {given.name} = {given.raw}")
    if scenario.kind == "call" and scenario.call is not None:
        call = scenario.call
        lines.append(f"when {call.target}({', '.join(call.raw_args)})")
        if call.expect_kind == "ok":
            lines.append("then ok")
        elif call.expect_kind == "error":
            if call.error_message:
                lines.append(f"then raises {call.error_type}: {call.error_message}")
            else:
                lines.append(f"then raises {call.error_type}")
        else:
            lines.append(f"then returns {call.raw_expected}")
    else:
        for step in scenario.steps:
            lines.extend(_render_command_step(step))
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
    blocks.extend(render_scenario(s) for s in spec.scenarios)
    return "\n\n".join(blocks) + "\n"


def save_vmd(spec: VMDSpec, path: Path, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_vmd(spec), encoding="utf-8")

    return path
