import json
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from conftest import make_plan, make_smd, make_task
from pydantic_ai import ModelRetry

from ossature.audit.graph import SpecGraph, SpecGraphEntry
from ossature.audit.planner import load_plan, merge_into_global_plan, write_plan
from ossature.build.builder import BuildContext, _check_writable
from ossature.config.loader import OssatureConfig, OutputConfig
from ossature.config.loader import TestConfig as _TestConfig
from ossature.models.plan import PlannerTask, SpecTaskPlan, TaskStatus
from ossature.parsers.vmd import parse_vmd
from ossature.verification.build import (
    _module_from_path,
    assemble_verify_task_prompt,
    build_verify_task,
    load_group,
)
from ossature.verification.fixture import fixture_filename, group_key, serialize_group
from ossature.verification.harness import render_python_harness
from ossature.verification.tasks import synthesize_verify_tasks

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

    def test_failing_cases_fail_cleanly_without_impl_files(self, tmp_path):
        wrong_impl = IMPL_TEXT.replace("return 9000", "return 8999")
        config, vmd_path = _project(tmp_path)
        (config.output_path / "src" / "whenwords" / "relative.py").write_text(wrong_impl)
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

        # No implementation tasks in the plan, so there is nothing for a
        # fixer to edit: the task must fail cleanly with no LLM call.
        result = build_verify_task(
            task, config, prompt, MagicMock(), MagicMock(), plan, {}, verbose=False
        )

        assert not result.success

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
