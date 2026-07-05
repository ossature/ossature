import json
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import tomli
from conftest import make_plan, make_smd, make_task
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import AgentRunError

from ossature.audit.graph import SpecGraph, SpecGraphEntry
from ossature.audit.planner import (
    incremental_merge_plan,
    load_plan,
    merge_into_global_plan,
    write_plan,
    write_task_definitions,
)
from ossature.build.builder import BuildContext, DefaultBuildBackend, _check_writable
from ossature.config.loader import OssatureConfig, OutputConfig
from ossature.config.loader import TestConfig as _TestConfig
from ossature.models.amd import AMDSpec, Component
from ossature.models.plan import PlannerTask, SpecTaskPlan, TaskStatus
from ossature.models.shared import Status
from ossature.parsers.vmd import parse_vmd
from ossature.verification.build import (
    _implementation_files,
    _module_from_path,
    assemble_verify_fix_prompt,
    assemble_verify_task_prompt,
    build_verify_task,
    load_group,
    module_candidates,
)
from ossature.verification.fixture import fixture_filename, group_key, serialize_group
from ossature.verification.harness import render_python_harness
from ossature.verification.tasks import VerifyTaskSpec, synthesize_verify_tasks

VMD_TEXT = dedent("""\
    @spec RELATIVE_TIME

    parse_duration(text) -> int
    compact | "2h30m" | 9000
    colon   | "1:30:00" | 5400
    empty   | ""      | !ValueError: empty
    bare    | "42"    | !ValueError

    duration(seconds, compact)
    verbose    | 3661 | false | "1 hour, 1 minute"
    compact_hm | 3661 | true  | "1h 1m"

    totals(data, amount:decimal) -> result ~struct ~decimal
    simple | {"a": 1} | "12.50" | {"total": "12.50", "ok": true}
""")

IMPL_TEXT = dedent("""\
    from collections import namedtuple
    from decimal import Decimal

    Result = namedtuple("Result", ["total", "ok"])


    def parse_duration(text):
        text = text.strip()
        if not text:
            raise ValueError("empty duration string")
        if text == "2h30m":
            return 9000
        if text == "1:30:00":
            return 5400
        raise ValueError("no parseable units")


    def duration(seconds, *, compact=False):
        if compact:
            return "1h 1m"
        return "1 hour, 1 minute"


    def totals(data, amount):
        assert isinstance(amount, Decimal)
        return Result(amount, True)
""")


def _project(tmp_path: Path) -> tuple[OssatureConfig, Path]:
    config = OssatureConfig(
        name="proj",
        root=tmp_path,
        output=OutputConfig(language="python"),
        test=_TestConfig(command=f"{shlex.quote(sys.executable)} -m pytest {{file}} -q"),
    )
    (tmp_path / "specs").mkdir()
    vmd_path = tmp_path / "specs" / "relative_time.vmd"
    vmd_path.write_text(VMD_TEXT)
    out = config.output_path
    (out / "src" / "whenwords").mkdir(parents=True)
    (out / "src" / "whenwords" / "__init__.py").write_text("")
    (out / "src" / "whenwords" / "relative.py").write_text(IMPL_TEXT)
    config.metadata_path.mkdir()
    return config, vmd_path


def _graph(spec_ids: list[str]) -> SpecGraph:
    return SpecGraph(
        specs=[SpecGraphEntry(id=s, file=f"{s.lower()}.smd", depends=[]) for s in spec_ids],
        levels=[spec_ids],
    )


class TestFixtureSerialization:
    def test_group_key_and_filename(self):
        vmd = parse_vmd(VMD_TEXT)
        parse_group = vmd.groups[0]
        duration_group = vmd.groups[1]

        assert group_key(parse_group) == "parse_duration/1"
        assert fixture_filename(parse_group) == "parse_duration.1.cases.json"
        assert group_key(duration_group) == "duration/2"

    def test_serialization_is_byte_stable(self):
        first = serialize_group(parse_vmd(VMD_TEXT).groups[0])
        second = serialize_group(parse_vmd(VMD_TEXT).groups[0])

        assert first == second

    def test_comment_and_alignment_edits_do_not_change_bytes(self):
        edited = VMD_TEXT.replace(
            'compact | "2h30m" | 9000',
            'compact   | "2h30m"   | 9000   # a new comment',
        )
        assert serialize_group(parse_vmd(VMD_TEXT).groups[0]) == serialize_group(
            parse_vmd(edited).groups[0]
        )

    def test_case_edit_changes_bytes(self):
        edited = VMD_TEXT.replace("9000", "9001")

        assert serialize_group(parse_vmd(VMD_TEXT).groups[0]) != serialize_group(
            parse_vmd(edited).groups[0]
        )

    def test_fixture_structure(self):
        data = json.loads(serialize_group(parse_vmd(VMD_TEXT).groups[0]))

        assert data["format"] == 1
        assert data["kind"] == "value"
        assert data["target"] == "parse_duration"
        assert [c["name"] for c in data["cases"]] == ["compact", "colon", "empty", "bare"]
        assert data["cases"][2]["expect"] == "error"
        assert data["cases"][2]["error_type"] == "ValueError"

    def test_cli_bytes_encoding(self):
        vmd = parse_vmd('@spec S\n\ntool(argv) ~cli\nbad | [!bytes[0x80,0xff]] | "" | 1\n')
        data = json.loads(serialize_group(vmd.groups[0]))

        assert data["cases"][0]["argv"] == [{"__bytes__": [128, 255]}]


class TestModulePaths:
    def test_module_from_path(self):
        assert _module_from_path("src/whenwords/relative.py") == "whenwords.relative"
        assert _module_from_path("pkg/mod.py") == "pkg.mod"
        assert _module_from_path("src/pkg/__init__.py") == "pkg"
        assert _module_from_path("tests/test_x.py") == ""
        assert _module_from_path("assets/logo.png") == ""


class TestSynthesis:
    def test_synthesizes_one_task_per_group(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)

        by_spec, warnings = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})

        assert warnings == []
        tasks = by_spec["RELATIVE_TIME"]
        assert [t.title for t in tasks] == [
            "Verify: parse_duration",
            "Verify: duration",
            "Verify: totals",
        ]
        first = tasks[0]
        assert first.outputs == [
            "checks/parse_duration.1.cases.json",
            "tests/test_checks_parse_duration.py",
        ]
        assert first.vmd_group == "parse_duration/1"
        assert first.vmd_file == "specs/relative_time.vmd"
        assert "pytest" in first.verify[0]
        assert "{file}" not in first.verify[0]

    def test_non_python_language_skips_value_groups_with_warning(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        config.output.language = "rust"
        vmd = parse_vmd(VMD_TEXT)

        by_spec, warnings = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})

        assert by_spec == {}
        assert len(warnings) == 3
        assert all("python output only" in w for w in warnings)

    def test_cli_groups_generate_for_any_language(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        config.output.language = "rust"
        cli_vmd = parse_vmd('@spec YEP\n\nyep(argv) ~cli\nbad | [!bytes[0xff]] | "" | 1\n')

        by_spec, warnings = synthesize_verify_tasks(config, [(vmd_path, cli_vmd)], {})

        assert warnings == []
        task = by_spec["YEP"][0]
        assert task.outputs == [
            "checks/yep.cli.cases.json",
            "checks/test_checks_yep_cli.py",
        ]
        assert task.vmd_group == "yep/cli"

    def test_python_cli_harness_stays_in_tests_dir(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        cli_vmd = parse_vmd('@spec YEP\n\nyep(argv) ~cli\nbad | [!bytes[0xff]] | "" | 1\n')

        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, cli_vmd)], {})

        assert by_spec["YEP"][0].outputs[1] == "tests/test_checks_yep_cli.py"

    def test_opaque_fixture_group_skipped_with_warning(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd('@spec S\n@fixture conn = !fresh db\n\nadd(conn, url)\na | "x" | 1\n')

        by_spec, warnings = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})

        assert by_spec == {}
        assert any("opaque fixtures" in w for w in warnings)


class TestPlanMerge:
    def _plan_with_verify(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        spec_plans = {
            "RELATIVE_TIME": SpecTaskPlan(
                tasks=[
                    PlannerTask(
                        title="Implement relative",
                        description="",
                        outputs=["src/whenwords/relative.py"],
                        depends_on=[],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    )
                ]
            )
        }
        smds = [make_smd("RELATIVE_TIME")]
        return merge_into_global_plan(spec_plans, _graph(["RELATIVE_TIME"]), smds, by_spec)

    def test_verify_tasks_appended_after_impl(self, tmp_path):
        plan = self._plan_with_verify(tmp_path)

        assert [t.kind for t in plan.tasks] == ["task", "verify", "verify", "verify"]
        assert [t.id for t in plan.tasks] == ["001", "002", "003", "004"]
        assert plan.tasks[1].depends_on == ["001"]

    def test_plan_round_trip_keeps_verify_fields(self, tmp_path):
        plan = self._plan_with_verify(tmp_path)
        plan_path = tmp_path / "plan.toml"
        write_plan(plan, plan_path)
        loaded = load_plan(plan_path)

        assert loaded is not None
        verify_task = loaded.tasks[1]
        assert verify_task.kind == "verify"
        assert verify_task.vmd_file == "specs/relative_time.vmd"
        assert verify_task.vmd_group == "parse_duration/1"

    def test_cross_spec_dependent_waits_for_verify_tasks(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        spec_plans = {
            "RELATIVE_TIME": SpecTaskPlan(
                tasks=[
                    PlannerTask(
                        title="Implement relative",
                        description="",
                        outputs=["src/whenwords/relative.py"],
                        depends_on=[],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    )
                ]
            ),
            "API": SpecTaskPlan(
                tasks=[
                    PlannerTask(
                        title="Implement api",
                        description="",
                        outputs=["src/api.py"],
                        depends_on=[],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    )
                ]
            ),
        }
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="RELATIVE_TIME", file="a.smd", depends=[]),
                SpecGraphEntry(id="API", file="b.smd", depends=["RELATIVE_TIME"]),
            ],
            levels=[["RELATIVE_TIME"], ["API"]],
        )
        smds = [make_smd("RELATIVE_TIME"), make_smd("API", depends=["RELATIVE_TIME"])]

        plan = merge_into_global_plan(spec_plans, graph, smds, by_spec)

        api_task = next(t for t in plan.tasks if t.spec == "API")
        last_verify = [t for t in plan.tasks if t.kind == "verify"][-1]
        assert last_verify.id in api_task.depends_on


class TestVerifyTaskPrompt:
    def test_prompt_embeds_fixture_and_changes_with_cases(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        task = plan.tasks[0]

        before = assemble_verify_task_prompt(task, config, plan, {})
        assert '"target":"parse_duration"' in before
        assert "9000" in before

        vmd_path.write_text(VMD_TEXT.replace("9000", "9001"))
        after = assemble_verify_task_prompt(task, config, plan, {})
        assert before != after

        # Comment-only edits keep the canonical serialization stable.
        vmd_path.write_text(VMD_TEXT + "\n# trailing comment\n")
        assert assemble_verify_task_prompt(task, config, plan, {}) == before


class TestWriteProtection:
    def test_checks_dir_is_read_only(self, tmp_path):
        ctx = SimpleNamespace(
            deps=BuildContext(
                output_dir=tmp_path,
                console=MagicMock(),
                status=MagicMock(),
            )
        )
        with pytest.raises(ModelRetry, match="read-only"):
            _check_writable(
                ctx, "checks/x.cases.json", (tmp_path / "checks/x.cases.json").resolve()
            )  # type: ignore[arg-type]

    def test_protected_paths_are_read_only(self, tmp_path):
        ctx = SimpleNamespace(
            deps=BuildContext(
                output_dir=tmp_path,
                console=MagicMock(),
                status=MagicMock(),
                protected_paths=["tests/test_checks_x.py"],
            )
        )
        with pytest.raises(ModelRetry, match="read-only"):
            _check_writable(
                ctx, "tests/test_checks_x.py", (tmp_path / "tests/test_checks_x.py").resolve()
            )  # type: ignore[arg-type]

    def test_ordinary_paths_are_writable(self, tmp_path):
        ctx = SimpleNamespace(
            deps=BuildContext(
                output_dir=tmp_path,
                console=MagicMock(),
                status=MagicMock(),
            )
        )
        _check_writable(ctx, "src/main.py", (tmp_path / "src/main.py").resolve())  # type: ignore[arg-type]


class TestBuildVerifyTask:
    def _run(self, tmp_path, vmd_text=VMD_TEXT, impl_text=IMPL_TEXT):
        config, vmd_path = _project(tmp_path)
        vmd_path.write_text(vmd_text)
        (config.output_path / "src" / "whenwords" / "relative.py").write_text(impl_text)
        vmd = parse_vmd(vmd_text)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        impl = make_task(
            "001", "RELATIVE_TIME", outputs=["src/whenwords/relative.py"], status=TaskStatus.DONE
        )
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        plan.tasks.insert(0, impl)
        task = next(t for t in plan.tasks if t.vmd_group == "parse_duration/1")
        prompt = assemble_verify_task_prompt(task, config, plan, {})
        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, verbose=False
        )
        return config, task, result

    def test_passing_cases_succeed_without_llm(self, tmp_path):
        config, task, result = self._run(tmp_path)

        assert result.success
        assert (config.output_path / task.outputs[0]).exists()
        assert (config.output_path / task.outputs[1]).exists()

    def test_no_module_candidates_fails_cleanly(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        task = next(t for t in plan.tasks if t.vmd_group == "parse_duration/1")
        prompt = assemble_verify_task_prompt(task, config, plan, {})

        # No implementation tasks in the plan means no importable modules,
        # so harness generation must fail cleanly with no LLM call.
        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, verbose=False
        )

        assert not result.success
        responses = list(config.metadata_path.glob("tasks/*/response.md"))
        assert "no importable modules" in responses[0].read_text()

    def test_failing_cases_fail_cleanly_without_impl_files(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        impl = make_task(
            "001", "RELATIVE_TIME", outputs=["src/whenwords/relative.py"], status=TaskStatus.DONE
        )
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        plan.tasks.insert(0, impl)
        # The implementation file is gone: verification fails, and with no
        # file on disk there is nothing for a fixer to edit, so the task
        # must fail cleanly without entering the fix loop.
        (config.output_path / "src" / "whenwords" / "relative.py").unlink()
        task = next(t for t in plan.tasks if t.vmd_group == "parse_duration/1")
        prompt = assemble_verify_task_prompt(task, config, plan, {})

        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, verbose=False
        )

        assert not result.success
        assert not list(config.metadata_path.glob("tasks/*/fix-1-prompt.md"))

    def test_fix_prompt_marks_large_and_skips_unreadable_files(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        task = next(t for t in plan.tasks if t.vmd_group == "parse_duration/1")
        big = config.output_path / "src" / "big.py"
        big.write_text("x = 1\n" * 300)
        binary = config.output_path / "src" / "blob.py"
        binary.write_bytes(b"\xff\xfe\x00binary")

        prompt = assemble_verify_fix_prompt(
            task, "boom", config, ["src/big.py", "src/blob.py"], "pytest"
        )

        assert "File is large" in prompt
        assert "blob.py" not in prompt

    def test_missing_group_fails_cleanly(self, tmp_path):
        config, task, _ = self._run(tmp_path)
        task.vmd_group = "nonexistent/9"
        plan = make_plan([task])

        result = build_verify_task(
            task,
            config,
            "prompt",
            MagicMock(),
            MagicMock(),
            plan,
            {},
            verbose=False,
        )

        assert not result.success

    def test_load_group_reports_parse_errors(self, tmp_path):
        config, task, _ = self._run(tmp_path)
        (config.root / task.vmd_file).write_text("f(x)\na | 1 | 2\n")

        group, error = load_group(task, config)

        assert group is None
        assert "no longer parses" in error


class TestGeneratedHarnessEndToEnd:
    def test_generated_harnesses_pass_against_correct_impl(self, tmp_path):
        _config, _vmd, result = self._run_all(tmp_path, IMPL_TEXT)

        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()

    def test_generated_harness_fails_against_wrong_impl(self, tmp_path):
        wrong = IMPL_TEXT.replace("return 9000", "return 8999")
        _config, _vmd, result = self._run_all(tmp_path, wrong)

        assert result.returncode != 0

    def test_count_meta_assert_catches_truncated_fixture(self, tmp_path):
        config, _vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        group = vmd.groups[0]
        fixture_rel = f"checks/{fixture_filename(group)}"
        harness_rel = "tests/test_checks_parse_duration.py"
        out = config.output_path

        data = json.loads(serialize_group(group))
        data["cases"] = data["cases"][:2]  # silently drop cases
        (out / "checks").mkdir()
        (out / "checks" / fixture_filename(group)).write_text(json.dumps(data))
        (out / "tests").mkdir()
        (out / harness_rel).write_text(
            render_python_harness(group, fixture_rel, ["whenwords.relative"])
        )

        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", harness_rel, "-q"],
            cwd=out,
            capture_output=True,
            timeout=120,
        )

        assert result.returncode != 0
        assert b"case_count" in result.stdout

    def test_cli_harness_resolves_binary_from_build_dir(self, tmp_path):
        config, _vmd_path = _project(tmp_path)
        cli_vmd = parse_vmd(
            "@spec YEP\n\n"
            "yep(argv) ~cli\n"
            'rejects | [!bytes[0xff,0xfe]] | "" | 1 | ~matches "(?i)utf-?8"\n'
            'plain   | ["--check-only"] | "y" | 0\n'
        )
        group = cli_vmd.groups[0]
        out = config.output_path
        binary = out / "target" / "release" / "yep"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "for arg in sys.argv[1:]:\n"
            "    try:\n"
            "        arg.encode('utf-8')\n"
            "    except UnicodeEncodeError:\n"
            "        print('error: invalid UTF-8 argument', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "print('y')\n"
        )
        binary.chmod(0o755)

        fixture_rel = f"checks/{fixture_filename(group)}"
        (out / "checks").mkdir()
        (out / fixture_rel).write_text(serialize_group(group))
        harness_rel = "checks/test_checks_yep_cli.py"
        (out / harness_rel).write_text(render_python_harness(group, fixture_rel, []))

        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", harness_rel, "-q"],
            cwd=out,
            capture_output=True,
            timeout=120,
        )

        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()

    def _run_all(self, tmp_path: Path, impl_text: str) -> tuple[Any, Any, Any]:
        config, _vmd_path = _project(tmp_path)
        (config.output_path / "src" / "whenwords" / "relative.py").write_text(impl_text)
        vmd = parse_vmd(VMD_TEXT)
        out = config.output_path
        (out / "checks").mkdir()
        (out / "tests").mkdir()
        for group in vmd.groups:
            fixture_rel = f"checks/{fixture_filename(group)}"
            (out / fixture_rel).write_text(serialize_group(group))
            harness = render_python_harness(group, fixture_rel, ["whenwords.relative"])
            (out / "tests" / f"test_checks_{group.name}.py").write_text(harness)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q"],
            cwd=out,
            capture_output=True,
            timeout=120,
        )
        return config, vmd, result


class FakeFixBackend:
    """Minimal backend for verify-task fix-loop tests: only fix() is used."""

    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.fix_calls = 0

    def fix(self, prompt, build_ctx, console, tracker, model_name):
        self.fix_calls += 1
        effect = self._side_effects.pop(0) if self._side_effects else None
        if isinstance(effect, AgentRunError):
            raise effect
        if callable(effect):
            effect()
        return "fixed"


class TestVerifyTaskFixLoop:
    def _setup(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        impl = make_task(
            "001", "RELATIVE_TIME", outputs=["src/whenwords/relative.py"], status=TaskStatus.DONE
        )
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        plan.tasks.insert(0, impl)
        task = next(t for t in plan.tasks if t.vmd_group == "parse_duration/1")
        # A controllable oracle: verification passes once the marker exists.
        marker = config.output_path / "fixed.marker"
        task.verify = [f"test -f {marker}"]
        return config, plan, task, marker

    def test_fixer_repairing_implementation_passes(self, tmp_path):
        config, plan, task, marker = self._setup(tmp_path)
        backend = FakeFixBackend([lambda: marker.write_text("ok")])
        prompt = assemble_verify_task_prompt(task, config, plan, {})

        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, backend=backend
        )

        assert result.success
        assert backend.fix_calls == 1
        prompts = list(config.metadata_path.glob("tasks/*/fix-1-prompt.md"))
        assert len(prompts) == 1
        content = prompts[0].read_text()
        assert "authoritative oracle" in content
        assert "src/whenwords/relative.py" in content

    def test_fix_attempts_exhaust_and_fail(self, tmp_path):
        config, plan, task, _marker = self._setup(tmp_path)
        backend = FakeFixBackend([None, None, None])
        prompt = assemble_verify_task_prompt(task, config, plan, {})

        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, backend=backend
        )

        assert not result.success
        assert backend.fix_calls == config.build.max_fix_attempts

    def test_fixer_agent_error_counts_as_attempt(self, tmp_path):
        config, plan, task, marker = self._setup(tmp_path)
        backend = FakeFixBackend([AgentRunError("boom"), lambda: marker.write_text("ok")])
        prompt = assemble_verify_task_prompt(task, config, plan, {})

        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, backend=backend
        )

        assert result.success
        assert backend.fix_calls == 2
        responses = list(config.metadata_path.glob("tasks/*/fix-1-response.md"))
        assert responses
        assert "agent error" in responses[0].read_text()


class TestVerifyTaskPromptBranches:
    def test_prompt_embeds_error_for_missing_group(self, tmp_path):
        config, plan, task, _ = TestVerifyTaskFixLoop()._setup(tmp_path)
        task.vmd_group = "nonexistent/9"

        prompt = assemble_verify_task_prompt(task, config, plan, {})

        assert "error:" in prompt
        assert "not found" in prompt

    def test_prompt_embeds_error_for_missing_file(self, tmp_path):
        config, plan, task, _ = TestVerifyTaskFixLoop()._setup(tmp_path)
        task.vmd_file = "specs/nope.vmd"

        prompt = assemble_verify_task_prompt(task, config, plan, {})

        assert "error: VMD file not found" in prompt

    def test_module_candidates_prefer_amd_target_file(self, tmp_path):
        config, plan, task, _ = TestVerifyTaskFixLoop()._setup(tmp_path)
        group, _ = load_group(task, config)
        amd = AMDSpec(
            title="A",
            spec_id="RELATIVE_TIME",
            status=Status.DRAFT,
            overview="o",
            components=[
                Component(
                    name="Relative",
                    path="src/whenwords/relative.py",
                    description="d",
                    interface="def parse_duration(text): ...",
                )
            ],
        )

        candidates = module_candidates(task, plan, group, [amd])

        assert candidates[0] == "whenwords.relative"

    def test_module_from_init_only_path_is_skipped(self):
        assert _module_from_path("src/__init__.py") == ""


class TestSynthesisBranches:
    def test_verify_command_without_placeholder_appends_path(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        config.test.command = "uv run pytest"
        vmd = parse_vmd(VMD_TEXT)

        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})

        first = by_spec["RELATIVE_TIME"][0]
        assert first.verify == ["uv run pytest tests/test_checks_parse_duration.py"]

    def test_vmd_path_outside_root_falls_back_to_str(self, tmp_path):
        config, _ = _project(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-outside.vmd"
        outside.write_text("@spec S\n\nf(x)\na | 1 | 2\n")
        vmd = parse_vmd(outside.read_text())

        by_spec, _ = synthesize_verify_tasks(config, [(outside, vmd)], {})

        assert by_spec["S"][0].vmd_file == str(outside)
        outside.unlink()

    def test_same_name_groups_get_arity_suffixed_harnesses(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(
            '@spec S\n\nduration(seconds)\na | 1 | "1s"\n\n'
            'duration(seconds, compact)\nb | 1 | true | "1s"\n'
        )

        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})

        harnesses = [t.outputs[1] for t in by_spec["S"]]
        assert harnesses == [
            "tests/test_checks_duration_1.py",
            "tests/test_checks_duration_2.py",
        ]


class TestPlanMergeBranches:
    def _verify_spec(self, target_file=""):
        return VerifyTaskSpec(
            spec_id="S",
            title="Verify: f",
            description="d",
            outputs=["checks/f.1.cases.json", "tests/test_checks_f.py"],
            verify=["python -m pytest tests/test_checks_f.py -q"],
            covers=[],
            vmd_file="specs/s.vmd",
            vmd_group="f/1",
            target_file=target_file,
        )

    def test_verify_task_depends_on_final_producer_of_target(self):
        spec_plans = {
            "S": SpecTaskPlan(
                tasks=[
                    PlannerTask(
                        title="Core",
                        description="",
                        outputs=["src/core.py"],
                        depends_on=[],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    ),
                    PlannerTask(
                        title="Extras",
                        description="",
                        outputs=["src/extras.py"],
                        depends_on=[1],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    ),
                ]
            )
        }
        by_spec = {"S": [self._verify_spec(target_file="src/core.py")]}

        plan = merge_into_global_plan(spec_plans, _graph(["S"]), [make_smd("S")], by_spec)

        verify_task = next(t for t in plan.tasks if t.kind == "verify")
        assert verify_task.depends_on == ["001"]

    def test_incremental_merge_carries_verify_task_status(self):
        old_verify = make_task(
            "002",
            "S",
            outputs=["checks/f.1.cases.json", "tests/test_checks_f.py"],
            status=TaskStatus.DONE,
        )
        old_verify.kind = "verify"
        old_impl = make_task("001", "S", outputs=["src/core.py"], status=TaskStatus.DONE)
        existing = make_plan([old_impl, old_verify])
        new_plans = {
            "S": SpecTaskPlan(
                tasks=[
                    PlannerTask(
                        title="Core",
                        description="",
                        outputs=["src/core.py"],
                        depends_on=[],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    )
                ]
            )
        }
        by_spec = {"S": [self._verify_spec(target_file="src/core.py")]}

        plan, id_remap, matched = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans=new_plans,
            changed_spec_ids={"S"},
            graph=_graph(["S"]),
            parsed_smds=[make_smd("S")],
            verify_tasks_by_spec=by_spec,
        )

        verify_task = next(t for t in plan.tasks if t.kind == "verify")
        assert verify_task.status == TaskStatus.DONE
        assert id_remap["002"] == verify_task.id
        assert "002" in matched


class TestVerifyTaskPersistence:
    def _plan(self, tmp_path):
        by_spec = {
            "S": [
                VerifyTaskSpec(
                    spec_id="S",
                    title="Verify: f",
                    description="d",
                    outputs=["checks/f.1.cases.json", "tests/test_checks_f.py"],
                    verify=["python -m pytest tests/test_checks_f.py -q"],
                    covers=["primary-action"],
                    vmd_file="specs/s.vmd",
                    vmd_group="f/1",
                )
            ]
        }
        spec_plans = {
            "S": SpecTaskPlan(
                tasks=[
                    PlannerTask(
                        title="Core",
                        description="",
                        outputs=["src/core.py"],
                        depends_on=[],
                        spec_refs=[],
                        arch_refs=[],
                        verify=["true"],
                    )
                ]
            )
        }
        return merge_into_global_plan(spec_plans, _graph(["S"]), [make_smd("S")], by_spec)

    def test_write_plan_persists_covers(self, tmp_path):
        plan = self._plan(tmp_path)
        plan_path = tmp_path / "plan.toml"

        write_plan(plan, plan_path)
        loaded = load_plan(plan_path)

        assert loaded is not None
        verify_task = next(t for t in loaded.tasks if t.kind == "verify")
        assert verify_task.covers == ["primary-action"]

    def test_task_definitions_persist_verify_fields(self, tmp_path):
        plan = self._plan(tmp_path)
        tasks_dir = tmp_path / "tasks"

        write_task_definitions(plan, tasks_dir)

        task_dir = next(d for d in tasks_dir.iterdir() if "verify" in d.name)
        with open(task_dir / "task.toml", "rb") as f:
            data = tomli.load(f)
        assert data["kind"] == "verify"
        assert data["vmd_file"] == "specs/s.vmd"
        assert data["vmd_group"] == "f/1"
        assert data["covers"] == ["primary-action"]


class TestImplementationFilesDedup:
    def test_shared_output_listed_once(self, tmp_path):
        config, vmd_path = _project(tmp_path)
        vmd = parse_vmd(VMD_TEXT)
        by_spec, _ = synthesize_verify_tasks(config, [(vmd_path, vmd)], {})
        scaffold = make_task(
            "001", "RELATIVE_TIME", outputs=["src/whenwords/relative.py"], status=TaskStatus.DONE
        )
        rewrite = make_task(
            "002", "RELATIVE_TIME", outputs=["src/whenwords/relative.py"], status=TaskStatus.DONE
        )
        plan = merge_into_global_plan(
            {"RELATIVE_TIME": SpecTaskPlan(tasks=[])},
            _graph(["RELATIVE_TIME"]),
            [make_smd("RELATIVE_TIME")],
            by_spec,
        )
        plan.tasks.insert(0, rewrite)
        plan.tasks.insert(0, scaffold)
        task = next(t for t in plan.tasks if t.kind == "verify")

        files = _implementation_files(task, plan, config)

        assert files == ["src/whenwords/relative.py"]


class TestDefaultBackendInstantiation:
    def test_fix_loop_builds_default_backend_when_none_given(self, tmp_path):
        config, plan, task, marker = TestVerifyTaskFixLoop()._setup(tmp_path)
        prompt = assemble_verify_task_prompt(task, config, plan, {})

        def fake_fix(self, fix_prompt, build_ctx, console, tracker, model_name):
            marker.write_text("ok")
            return "fixed"

        with patch.object(DefaultBuildBackend, "fix", fake_fix):
            result = build_verify_task(task, config, prompt, MagicMock(), MagicMock(), plan, {})

        assert result.success
