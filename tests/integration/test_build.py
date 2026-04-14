import json
import os
from pathlib import Path

from click.testing import CliRunner
from conftest import make_plan, make_task

from ossature.audit.planner import write_plan
from ossature.cli.main import cli
from ossature.models.plan import TaskStatus


class TestBuildDryRun:
    def _run(self, runner, project_dir, args=None):
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            return runner.invoke(cli, args or ["build", "--dry-run"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

    def _write_plan(self, project_dir, tasks):
        plan = make_plan(tasks)
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)
        return plan

    def test_no_plan_exits_with_error(self, runner: CliRunner, project_dir: Path):
        result = self._run(runner, project_dir)

        assert result.exit_code == 1

    def test_all_completed_shows_message(self, runner: CliRunner, project_dir: Path):
        self._write_plan(
            project_dir,
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "AUTH", status=TaskStatus.DONE),
            ],
        )

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "completed" in result.output.lower()

    def test_pending_tasks_listed(self, runner: CliRunner, project_dir: Path):
        self._write_plan(
            project_dir,
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "AUTH"),
                make_task("3", "AUTH"),
            ],
        )

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "2" in result.output
        assert "3" in result.output
        # Done task should not appear in dry-run listing
        assert "1" not in result.output

    def test_failed_tasks_listed(self, runner: CliRunner, project_dir: Path):
        self._write_plan(
            project_dir,
            [
                make_task("1", "AUTH", status=TaskStatus.FAILED),
                make_task("2", "AUTH"),
            ],
        )

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert "failed" in result.output.lower()

    def test_does_not_invoke_llm(self, runner: CliRunner, project_dir: Path):
        """Dry run must not call any LLM-backed execution."""
        self._write_plan(project_dir, [make_task("1", "AUTH")])

        # If execute_build were called it would raise because there's no API key;
        # a clean exit proves it was never invoked.
        result = self._run(runner, project_dir)

        assert result.exit_code == 0


class TestBuildDryRunJsonOutput:
    def _run(self, runner, project_dir, args=None):
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            return runner.invoke(
                cli,
                args or ["-o", "json", "build", "--dry-run"],
                catch_exceptions=False,
            )
        finally:
            os.chdir(old_cwd)

    def _write_plan(self, project_dir, tasks):
        plan = make_plan(tasks)
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(plan, plan_path)

    def test_all_done_returns_empty_actionable(self, runner: CliRunner, project_dir: Path):
        self._write_plan(
            project_dir,
            [make_task("1", "AUTH", status=TaskStatus.DONE)],
        )

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["actionable_tasks"] == []
        assert data["pending"] == 0
        assert data["failed"] == 0

    def test_pending_tasks_in_json(self, runner: CliRunner, project_dir: Path):
        self._write_plan(
            project_dir,
            [
                make_task("1", "AUTH", status=TaskStatus.DONE),
                make_task("2", "AUTH"),
                make_task("3", "API", status=TaskStatus.FAILED),
            ],
        )

        result = self._run(runner, project_dir)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["pending"] == 1
        assert data["failed"] == 1
        task_ids = [t["id"] for t in data["actionable_tasks"]]
        assert "2" in task_ids
        assert "3" in task_ids
        assert "1" not in task_ids

    def test_task_fields_present(self, runner: CliRunner, project_dir: Path):
        self._write_plan(project_dir, [make_task("1", "AUTH")])

        result = self._run(runner, project_dir)

        task = json.loads(result.output)["actionable_tasks"][0]
        assert "id" in task
        assert "spec" in task
        assert "title" in task
        assert "status" in task
