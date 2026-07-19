"""End-to-end audit runs on projects that carry a VMD file.

All agents are mocked, so these tests exercise the real command flow: VMD
parsing, manifest tracking, audit context assembly, planner prompt assembly,
and the deterministic verify-task merge.
"""

import shlex
import sys
from pathlib import Path

from click.testing import CliRunner
from helpers import make_spec_task_plan, patch_all_agents, run_in_project, write_smd

from ossature.audit.manifest import read_manifest
from ossature.audit.planner import load_plan, write_plan
from ossature.models.plan import TaskStatus

CORE_PLAN = make_spec_task_plan(
    [
        {"title": "Core: Module", "outputs": ["src/core.py"], "verify": "true"},
    ]
)

VMD = """\
@spec AUTH

core_requirement(input_data)
basic | "sample" | "processed"
"""


def _write_vmd(project_dir: Path) -> Path:
    vmd_path = project_dir / "specs" / "auth.vmd"
    vmd_path.write_text(VMD)
    return vmd_path


class TestAuditWithVMD:
    def test_audit_appends_verify_task(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        _write_vmd(project_dir)

        with patch_all_agents({"AUTH": CORE_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        verify_tasks = [t for t in plan.tasks if t.kind == "verify"]
        assert len(verify_tasks) == 1
        task = verify_tasks[0]
        assert task.vmd_group == "core_requirement/1"
        assert task.outputs == [
            "checks/auth.core_requirement.1.cases.json",
            "tests/test_checks_auth_core_requirement.py",
        ]
        assert task.depends_on == [plan.tasks[0].id]

    def test_audit_tracks_vmd_in_manifest(self, runner: CliRunner, project_dir: Path):
        # Regression: the end-of-audit manifest refresh must keep the VMD
        # checksum, or every following audit re-audits the spec for no reason.
        write_smd(project_dir, "AUTH", "Authentication Module")
        _write_vmd(project_dir)

        with patch_all_agents({"AUTH": CORE_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        manifest = read_manifest(project_dir / ".ossature" / "manifest.toml")
        assert manifest is not None
        assert any(key.endswith("auth.vmd") for key in manifest.sources)

    def test_second_audit_sees_no_changes(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        _write_vmd(project_dir)

        with patch_all_agents({"AUTH": CORE_PLAN}):
            first = run_in_project(runner, project_dir, ["audit"])
            second = run_in_project(runner, project_dir, ["audit"])

        assert first.exit_code == 0
        assert second.exit_code == 0
        assert "No changes detected" in second.output

    def test_vmd_reaches_audit_and_planner_prompts(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        _write_vmd(project_dir)

        with patch_all_agents({"AUTH": CORE_PLAN}):
            run_in_project(runner, project_dir, ["audit"])

        audit_prompt = (project_dir / ".ossature" / "audits" / "AUTH" / "prompt.md").read_text()
        assert "Verification Cases (VMD, read-only)" in audit_prompt
        assert "core_requirement(input_data)" in audit_prompt

        planner_prompt = (project_dir / ".ossature" / "planners" / "AUTH" / "prompt.md").read_text()
        assert "Author Verification Cases (VMD)" in planner_prompt

    def test_planner_prompt_lists_scenario_names_and_covers(
        self, runner: CliRunner, project_dir: Path
    ):
        # The planner never sees the VMD itself, so its dedup signal is the
        # target line: scenario names plus the covered requirements.
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            "@spec AUTH\n"
            "\n"
            '@covers "Core Requirement"\n'
            "scenario rejects a bad flag:\n"
            "when $ mytool --nope\n"
            "then exit 2\n"
            'then stderr has "usage"\n'
        )

        with patch_all_agents({"AUTH": CORE_PLAN}):
            run_in_project(runner, project_dir, ["audit"])

        planner_prompt = (project_dir / ".ossature" / "planners" / "AUTH" / "prompt.md").read_text()
        assert "rejects a bad flag" in planner_prompt
        assert "[covers: Core Requirement]" in planner_prompt
        assert "set `covers` on those test tasks" in planner_prompt

    def test_vmd_edit_retriggers_audit(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        vmd_path = _write_vmd(project_dir)

        with patch_all_agents({"AUTH": CORE_PLAN}):
            run_in_project(runner, project_dir, ["audit"])
            vmd_path.write_text(VMD.replace('"processed"', '"changed"'))
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        assert "auth.vmd has changed" in result.output

    def test_non_python_value_groups_warn(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        _write_vmd(project_dir)
        config_path = project_dir / "ossature.toml"
        config_path.write_text(config_path.read_text().replace('"python"', '"rust"'))

        with patch_all_agents({"AUTH": CORE_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        assert "python output only" in result.output
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        assert not [t for t in plan.tasks if t.kind == "verify"]


class TestBuildWithVMD:
    def test_build_runs_verify_task_without_llm(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        _write_vmd(project_dir)
        config_path = project_dir / "ossature.toml"
        command = f"{shlex.quote(sys.executable)} -m pytest {{file}} -q"
        config_path.write_text(config_path.read_text() + f'\n[test]\ncommand = "{command}"\n')

        with patch_all_agents({"AUTH": CORE_PLAN}):
            audit = run_in_project(runner, project_dir, ["audit"])
        assert audit.exit_code == 0

        # The mocked implementer writes nothing, so seed the implementation
        # the verify task's harness imports and checks.
        core = project_dir / "output" / "src" / "core.py"
        core.parent.mkdir(parents=True)
        core.write_text("def core_requirement(input_data):\n    return 'processed'\n")

        with patch_all_agents({"AUTH": CORE_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0, result.output
        assert (project_dir / "output" / "checks" / "auth.core_requirement.1.cases.json").exists()
        assert (project_dir / "output" / "tests" / "test_checks_auth_core_requirement.py").exists()
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        verify_task = next(t for t in plan.tasks if t.kind == "verify")
        assert verify_task.status.value == "done"

        # Flip the implementation task back to pending so the second build
        # walks the loop again and reaches the DONE verify task, exercising
        # its deterministic prompt assembly on the cached path.
        plan.tasks[0].status = TaskStatus.PENDING
        write_plan(plan, project_dir / ".ossature" / "plan.toml")

        with patch_all_agents({"AUTH": CORE_PLAN}):
            second = run_in_project(runner, project_dir, ["build", "--auto"])
        assert second.exit_code == 0
        plan2 = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan2 is not None
        assert all(t.status == TaskStatus.DONE for t in plan2.tasks)
