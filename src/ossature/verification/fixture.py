import json
from typing import Any

from ossature.models.vmd import Group

FIXTURE_FORMAT = 1
FIXTURE_DIR = "checks"


def group_key(group: Group) -> str:
    """Stable identifier for a group within its spec: name/arity or name/cli."""
    if group.kind == "cli":
        return f"{group.name}/cli"
    return f"{group.name}/{group.arity}"


def fixture_filename(group: Group) -> str:
    """Deterministic fixture basename, unique per group key within a spec."""
    if group.kind == "cli":
        return f"{group.name}.cli.cases.json"
    return f"{group.name}.{group.arity}.cases.json"


def _encode_argv(argv: list[Any]) -> list[Any]:
    encoded: list[Any] = []
    for item in argv:
        if isinstance(item, bytes):
            encoded.append({"__bytes__": list(item)})
        else:
            encoded.append(item)
    return encoded


def serialize_group(group: Group) -> str:
    """Serialize a group's author-written cases to canonical JSON.

    Byte-stable for the same parsed group: sorted keys, no whitespace, ASCII
    only. This file is the oracle the generated harness loads; it is written
    by Ossature core code, never by a model.
    """
    data: dict[str, Any] = {
        "format": FIXTURE_FORMAT,
        "kind": group.kind,
        "target": group.name,
    }
    if group.kind == "cli":
        data["cases"] = [
            {
                "name": c.name,
                "argv": _encode_argv(c.argv),
                "stdout": c.stdout,
                "stdout_is_pattern": c.stdout_is_pattern,
                "exit": c.exit_code,
                "stderr": c.stderr,
                "stderr_is_pattern": c.stderr_is_pattern,
            }
            for c in group.cli_cases
        ]
    else:
        data["params"] = [
            {"name": p.name, "type": p.type} for p in group.params if not p.opaque_fixture
        ]
        data["compare"] = {
            "modes": sorted(group.compare_modes),
            "approx_tol": group.approx_tol,
        }
        data["cases"] = [
            {
                "name": c.name,
                "inputs": c.inputs,
                "expect": c.expect_kind,
                "expected": c.expected,
                "error_type": c.error_type,
                "error_message": c.error_message,
            }
            for c in group.cases
        ]
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
