import json
import re

from ossature.models.vmd import Group

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

_CLI_TEMPLATE = '''\
"""Generated verification harness for the __TARGET__ command. Do not edit.

The cases come from __FIXTURE__, which is generated from the author-written
verification spec. The expected values are author-owned; changing this file
does not change what correct means.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DATA = json.loads((_ROOT / __FIXTURE_LITERAL__).read_text())
_CASES = _DATA["cases"]
_EXPECTED_CASE_COUNT = __COUNT__


def _resolve_program():
    name = _DATA["target"]
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


_PROGRAM = _resolve_program()


def _decode_arg(item):
    if isinstance(item, dict) and "__bytes__" in item:
        return bytes(item["__bytes__"])
    return item


def _run(case):
    argv = [_decode_arg(a) for a in case["argv"]]
    return subprocess.run(
        [_PROGRAM, *argv],
        capture_output=True,
        cwd=_ROOT,
        timeout=60,
    )


def _assert_stream(actual_bytes, expected, is_pattern, channel):
    actual = actual_bytes.decode("utf-8", errors="replace")
    if is_pattern:
        assert re.search(expected, actual), (
            f"{channel} {actual!r} does not match pattern {expected!r}"
        )
        return
    trimmed = actual[:-1] if actual.endswith("\\n") else actual
    assert expected in (actual, trimmed), (
        f"{channel} {actual!r} does not equal {expected!r}"
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["name"])
def test___SAFE_NAME___cases(case):
    proc = _run(case)
    if case["exit"] is not None:
        assert proc.returncode == case["exit"], (
            f"exit code {proc.returncode}, expected {case['exit']}; "
            f"stderr: {proc.stderr.decode('utf-8', errors='replace')!r}"
        )
    if case["stdout"] is not None:
        _assert_stream(proc.stdout, case["stdout"], case["stdout_is_pattern"], "stdout")
    if case["stderr"] is not None:
        _assert_stream(proc.stderr, case["stderr"], case["stderr_is_pattern"], "stderr")


def test___SAFE_NAME___case_count():
    assert len(_CASES) == _EXPECTED_CASE_COUNT
'''


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def render_python_harness(
    group: Group,
    fixture_relpath: str,
    module_candidates: list[str],
) -> str:
    """Render the deterministic pytest harness for a group.

    fixture_relpath is the fixture's output-root-relative path; the module
    candidates are tried in order until one exposes the target callable.
    """
    if group.kind == "cli":
        template = _CLI_TEMPLATE
        count = len(group.cli_cases)
    else:
        template = _VALUE_TEMPLATE
        count = len(group.cases)
    return (
        template.replace("__FIXTURE_LITERAL__", json.dumps(fixture_relpath))
        .replace("__FIXTURE__", fixture_relpath)
        .replace("__TARGET__", group.name)
        .replace("__COUNT__", str(count))
        .replace("__MODULES__", json.dumps(module_candidates))
        .replace("__SAFE_NAME__", _safe_name(group.name))
    )
