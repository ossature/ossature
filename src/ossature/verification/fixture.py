import json
from typing import Any

from ossature.models.vmd import CommandStep, Group, Scenario

FIXTURE_FORMAT = 1
FIXTURE_DIR = "checks"

# The vmd_group key a scenarios verify task carries in the plan; scenarios
# are bundled per VMD file rather than per callable.
SCENARIOS_GROUP = "@scenarios"


def group_key(group: Group) -> str:
    """Stable identifier for a group within its spec: name/arity."""
    return f"{group.name}/{group.arity}"


def fixture_filename(group: Group) -> str:
    """Deterministic fixture basename, unique per group key within a spec."""
    return f"{group.name}.{group.arity}.cases.json"


def scenarios_fixture_filename(stem: str) -> str:
    """Fixture basename for a VMD file's scenarios bundle.

    The 'scenarios.' prefix keeps it out of the '<func>.<arity>' namespace
    group fixtures use.
    """
    return f"scenarios.{stem}.cases.json"


def _encode_argv(argv: list[Any]) -> list[Any]:
    encoded: list[Any] = []
    for item in argv:
        if isinstance(item, bytes):
            encoded.append({"__bytes__": list(item)})
        else:
            encoded.append(item)
    return encoded


def _encode_command_step(step: CommandStep) -> dict[str, Any]:
    return {
        "argv": _encode_argv(step.argv),
        "stdout_lines": step.stdout_lines,
        "stdout": step.stdout,
        "stdout_mode": step.stdout_mode,
        "exit": step.exit_code,
        "stderr": step.stderr,
        "stderr_mode": step.stderr_mode,
    }


def serialize_group(group: Group) -> str:
    """Serialize a group's author-written cases to canonical JSON.

    Byte-stable for the same parsed group: sorted keys, no whitespace, ASCII
    only. The generated harness loads this file to get its expected values;
    it is written by Ossature core code, never by a model.
    """
    data: dict[str, Any] = {
        "format": FIXTURE_FORMAT,
        "kind": "value",
        "target": group.name,
        "params": [{"name": p.name, "type": p.type} for p in group.params if not p.opaque_fixture],
        "compare": {
            "modes": sorted(group.compare_modes),
            "approx_tol": group.approx_tol,
        },
        "cases": [
            {
                "name": c.name,
                "inputs": c.inputs,
                "expect": c.expect_kind,
                "expected": c.expected,
                "error_type": c.error_type,
                "error_message": c.error_message,
            }
            for c in group.cases
        ],
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def serialize_scenarios(scenarios: list[Scenario]) -> str:
    """Serialize a VMD file's scenarios to canonical JSON, same trust class
    as serialize_group: deterministic core code, never a model."""
    cases: list[dict[str, Any]] = []
    for scenario in scenarios:
        if scenario.kind == "call" and scenario.call is not None:
            call = scenario.call
            cases.append(
                {
                    "name": scenario.slug,
                    "title": scenario.name,
                    "kind": "call",
                    "target": call.target,
                    "args": call.args,
                    "expect": call.expect_kind,
                    "expected": call.expected,
                    "error_type": call.error_type,
                    "error_message": call.error_message,
                }
            )
        else:
            cases.append(
                {
                    "name": scenario.slug,
                    "title": scenario.name,
                    "kind": "command",
                    "steps": [_encode_command_step(s) for s in scenario.steps],
                }
            )
    data: dict[str, Any] = {
        "format": FIXTURE_FORMAT,
        "kind": "scenarios",
        "target": SCENARIOS_GROUP,
        "cases": cases,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
