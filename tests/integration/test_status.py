import os
from pathlib import Path

from click.testing import CliRunner
from conftest import make_plan, make_task

from ossature.audit.planner import write_plan
from ossature.cli.main import cli
from ossature.models.plan import TaskStatus


class TestStatusCommand:
    def _run(self, runner, project_dir, args=None):
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            return runner.invoke(cli, args or ["status"])
        finally:
            os.chdir(old_cwd)

    def test_no_plan_shows_message(self, runner: CliRunner, project_dir: Path):
        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "No build plan" in result.output

    def test_single_spec_all_pending(self, runner: CliRunner, project_dir: Path):
        plan = make_plan(
            [
                make_task("1", "AUTH"),
                make_task("2", "AUTH"),
                make_task("3", "AUTH"),
            ]
        )
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "3" in result.output

    def test_mixed_status(self, runner: CliRunner, project_dir: Path):
        plan = make_plan(
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "AUTH", status=TaskStatus.FAILED),
                make_task("3", "AUTH", status=TaskStatus.PENDING),
            ]
        )
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "AUTH" in result.output
        assert "3" in result.output

    def test_all_done_shows_checkmark(self, runner: CliRunner, project_dir: Path):
        plan = make_plan(
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "AUTH", status=TaskStatus.DONE),
            ]
        )
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "✓" in result.output

    def test_failed_task_shows_retry_hint(self, runner: CliRunner, project_dir: Path):
        plan = make_plan(
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "AUTH", status=TaskStatus.FAILED),
            ]
        )
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "ossature retry" in result.output

    def test_multi_spec_status(self, runner: CliRunner, project_dir: Path):
        plan = make_plan(
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "API", status=TaskStatus.PENDING),
            ]
        )
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "AUTH" in result.output
        assert "API" in result.output
