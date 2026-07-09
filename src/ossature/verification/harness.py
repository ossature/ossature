import json
import re

from ossature.models.vmd import Group, Scenario

# The harness files below are deterministic templates: Ossature renders them
# with token substitution and no model involvement, so nothing in the grading
# path is model-authored. The expected values live only in the fixture the
# harness loads; the harness itself carries none of them. The case-count
# assert defends against a loader that silently runs fewer cases than the
# author wrote.

_VALUE_TEMPLATE = '''\
"""Generated verification harness for __TARGET__. Do not edit.

The cases come from __FIXTURE__, which is generated from the author-written
verification spec. The expected values are author-owned; changing this file
does not change what correct means.
"""

import importlib
import inspect
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _cand in (_ROOT / "src", _ROOT):
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

_DATA = json.loads((_ROOT / __FIXTURE_LITERAL__).read_text())
_CASES = _DATA["cases"]
_EXPECTED_CASE_COUNT = __COUNT__
_MODULE_CANDIDATES = __MODULES__
_PARAMS = _DATA["params"]
_MODES = set(_DATA["compare"]["modes"])
_APPROX_TOL = _DATA["compare"]["approx_tol"]


def _resolve_target():
    failures = []
    for name in _MODULE_CANDIDATES:
        try:
            module = importlib.import_module(name)
        except ImportError as e:
            failures.append(f"{name}: {e}")
            continue
        if hasattr(module, _DATA["target"]):
            return getattr(module, _DATA["target"])
        failures.append(f"{name}: no attribute {_DATA['target']!r}")
    raise RuntimeError(
        "target %r not found; tried %s" % (_DATA["target"], "; ".join(failures))
    )


_TARGET = _resolve_target()


def _coerce(value, param):
    if param["type"] == "decimal" and isinstance(value, (str, int, float)):
        return Decimal(str(value))
    return value


def _call(case):
    values = [_coerce(v, p) for v, p in zip(case["inputs"], _PARAMS)]
    try:
        sig = inspect.signature(_TARGET)
    except (TypeError, ValueError):
        return _TARGET(*values)
    real = sig.parameters
    args = []
    kwargs = {}
    for value, declared in zip(values, _PARAMS):
        param = real.get(declared["name"])
        if param is None or param.kind is inspect.Parameter.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[declared["name"]] = value
    return _TARGET(*args, **kwargs)


def _normalize(value):
    if hasattr(value, "_asdict"):
        return {k: _normalize(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _to_decimal(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError:
            return value
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_decimal(v) for v in value]
    return value


def _key(value):
    return json.dumps(value, sort_keys=True, default=str)


def _assert_match(actual, expected):
    if "struct" in _MODES:
        actual = _normalize(actual)
        expected = _normalize(expected)
    if "decimal" in _MODES:
        actual = _to_decimal(actual)
        expected = _to_decimal(expected)
    if "matches" in _MODES:
        assert re.search(expected, str(actual)), (
            f"output {actual!r} does not match pattern {expected!r}"
        )
        return
    if "unordered" in _MODES:
        assert sorted(_key(v) for v in actual) == sorted(_key(v) for v in expected)
        return
    if "approx" in _MODES:
        if _APPROX_TOL is None:
            assert actual == pytest.approx(expected)
        else:
            assert actual == pytest.approx(expected, abs=_APPROX_TOL)
        return
    assert actual == expected


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["name"])
def test___SAFE_NAME___cases(case):
    if case["expect"] == "error":
        with pytest.raises(Exception) as exc_info:
            _call(case)
        actual_type = type(exc_info.value).__name__
        expected_type = case["error_type"].rsplit(".", 1)[-1]
        assert actual_type == expected_type, (
            f"expected {expected_type}, got {actual_type}: {exc_info.value}"
        )
        if case["error_message"]:
            assert case["error_message"] in str(exc_info.value)
    elif case["expect"] == "ok":
        _call(case)
    else:
        _assert_match(_call(case), case["expected"])


def test___SAFE_NAME___case_count():
    assert len(_CASES) == _EXPECTED_CASE_COUNT
'''

_SCENARIOS_TEMPLATE = '''\
"""Generated verification harness for the __TARGET__ scenarios. Do not edit.

The scenarios come from __FIXTURE__, which is generated from the
author-written verification spec. The expected values are author-owned;
changing this file does not change what correct means.
"""

import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _cand in (_ROOT / "src", _ROOT):
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

_DATA = json.loads((_ROOT / __FIXTURE_LITERAL__).read_text())
_CASES = _DATA["cases"]
_EXPECTED_CASE_COUNT = __COUNT__
_MODULE_CANDIDATES = __MODULES__


def _resolve_callable(name):
    failures = []
    for module_name in _MODULE_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            failures.append(f"{module_name}: {e}")
            continue
        if hasattr(module, name):
            return getattr(module, name)
        failures.append(f"{module_name}: no attribute {name!r}")
    raise RuntimeError("target %r not found; tried %s" % (name, "; ".join(failures)))


def _resolve_program(name):
    candidates = [
        Path("target/release") / name,
        Path("target/debug") / name,
        Path("zig-out/bin") / name,
        Path("build") / name,
        Path("bin") / name,
        Path(name),
    ]
    for rel in candidates:
        full = _ROOT / rel
        if full.is_file() and os.access(full, os.X_OK):
            return str(full)
    return name


def _decode_arg(item):
    if isinstance(item, dict) and "__bytes__" in item:
        return bytes(item["__bytes__"])
    return item


def _run_call(case):
    target = _resolve_callable(case["target"])
    args = case["args"]
    try:
        sig = inspect.signature(target)
        params = list(sig.parameters.values())
    except (TypeError, ValueError):
        return target(*args)
    positional = []
    kwargs = {}
    for value, param in zip(args, params):
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = value
        else:
            positional.append(value)
    positional.extend(args[len(params) :])
    return target(*positional, **kwargs)


def _assert_stream(actual_bytes, check, channel, scenario):
    actual = actual_bytes.decode("utf-8", errors="replace")
    mode = check["mode"]
    expected = check["value"]
    label = f"{scenario}: {channel}"
    if mode == "empty":
        assert actual == "", f"{label} expected empty, got {actual!r}"
    elif mode == "has":
        assert expected in actual, f"{label} {actual!r} does not contain {expected!r}"
    elif mode == "matches":
        assert re.search(expected, actual), (
            f"{label} {actual!r} does not match pattern {expected!r}"
        )
    else:
        trimmed = actual[:-1] if actual.endswith("\\n") else actual
        assert expected in (actual, trimmed), f"{label} {actual!r} != {expected!r}"


def _run_command_scenario(case, tmp_path):
    for index, step in enumerate(case["steps"]):
        argv = [_decode_arg(a) for a in step["argv"]]
        argv[0] = _resolve_program(argv[0]) if isinstance(argv[0], str) else argv[0]
        proc = subprocess.run(argv, capture_output=True, cwd=tmp_path, timeout=60)
        label = f"{case['name']} step {index + 1}"
        assert proc.returncode == step["exit"], (
            f"{label}: exit {proc.returncode}, expected {step['exit']}; "
            f"stderr: {proc.stderr.decode('utf-8', errors='replace')!r}"
        )
        if step["stdout_lines"] is not None:
            expected = "\\n".join(step["stdout_lines"])
            actual = proc.stdout.decode("utf-8", errors="replace")
            trimmed = actual[:-1] if actual.endswith("\\n") else actual
            assert expected in (actual, trimmed), (
                f"{label}: stdout {actual!r} != {expected!r}"
            )
        if step["stdout_mode"]:
            _assert_stream(
                proc.stdout,
                {"mode": step["stdout_mode"], "value": step["stdout"]},
                "stdout",
                label,
            )
        if step["stderr_mode"]:
            _assert_stream(
                proc.stderr,
                {"mode": step["stderr_mode"], "value": step["stderr"]},
                "stderr",
                label,
            )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["name"])
def test___SAFE_NAME___scenarios(case, tmp_path):
    if case["kind"] == "command":
        _run_command_scenario(case, tmp_path)
        return
    if case["expect"] == "error":
        with pytest.raises(Exception) as exc_info:
            _run_call(case)
        actual_type = type(exc_info.value).__name__
        expected_type = case["error_type"].rsplit(".", 1)[-1]
        assert actual_type == expected_type, (
            f"expected {expected_type}, got {actual_type}: {exc_info.value}"
        )
        if case["error_message"]:
            assert case["error_message"] in str(exc_info.value)
    elif case["expect"] == "ok":
        _run_call(case)
    else:
        assert _run_call(case) == case["expected"]


def test___SAFE_NAME___scenario_count():
    assert len(_CASES) == _EXPECTED_CASE_COUNT
'''


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def render_python_harness(
    group: Group,
    fixture_relpath: str,
    module_candidates: list[str],
) -> str:
    """Render the deterministic pytest harness for a table group.

    fixture_relpath is the fixture's output-root-relative path; the module
    candidates are tried in order until one exposes the target callable.
    """
    return (
        _VALUE_TEMPLATE.replace("__FIXTURE_LITERAL__", json.dumps(fixture_relpath))
        .replace("__FIXTURE__", fixture_relpath)
        .replace("__TARGET__", group.name)
        .replace("__COUNT__", str(len(group.cases)))
        .replace("__MODULES__", json.dumps(module_candidates))
        .replace("__SAFE_NAME__", _safe_name(group.name))
    )


def render_scenarios_harness(
    scenarios: list[Scenario],
    stem: str,
    fixture_relpath: str,
    module_candidates: list[str],
) -> str:
    """Render the deterministic pytest harness for a VMD file's scenarios.

    Command scenarios run each step in a pytest-provided temp directory, so
    file state flows between steps and never between scenarios. Call
    scenarios resolve their target from the module candidates per case.
    """
    return (
        _SCENARIOS_TEMPLATE.replace("__FIXTURE_LITERAL__", json.dumps(fixture_relpath))
        .replace("__FIXTURE__", fixture_relpath)
        .replace("__TARGET__", stem)
        .replace("__COUNT__", str(len(scenarios)))
        .replace("__MODULES__", json.dumps(module_candidates))
        .replace("__SAFE_NAME__", _safe_name(stem))
    )
