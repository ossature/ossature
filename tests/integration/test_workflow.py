from pathlib import Path

from click.testing import CliRunner
from helpers import make_spec_task_plan, patch_all_agents, run_in_project, write_smd

from ossature.models.plan import SpecTaskPlan, TaskStatus

# Canned plans

AUTH_PLAN = make_spec_task_plan(
    [
        {"title": "Auth: Scaffold", "outputs": ["src/auth/__init__.py"]},
        {"title": "Auth: Models", "outputs": ["src/auth/models.py"], "depends_on": [1]},
        {"title": "Auth: Service", "outputs": ["src/auth/service.py"], "depends_on": [2]},
    ]
)

API_PLAN = make_spec_task_plan(
    [
        {"title": "API: Scaffold", "outputs": ["src/api/__init__.py"]},
        {"title": "API: Routes", "outputs": ["src/api/routes.py"], "depends_on": [1]},
    ]
)

DB_PLAN = make_spec_task_plan(
    [
        {"title": "DB: Scaffold", "outputs": ["src/db/__init__.py"]},
        {"title": "DB: Models", "outputs": ["src/db/models.py"], "depends_on": [1]},
    ]
)

# Alternate AUTH plan (for replan tests)
AUTH_PLAN_V2 = make_spec_task_plan(
    [
        {"title": "Auth: Scaffold v2", "outputs": ["src/auth/__init__.py"]},
        {"title": "Auth: Tokens v2", "outputs": ["src/auth/tokens.py"], "depends_on": [1]},
        {"title": "Auth: Service v2", "outputs": ["src/auth/service.py"], "depends_on": [2]},
        {"title": "Auth: Tests v2", "outputs": ["tests/test_auth.py"], "depends_on": [3]},
    ]
)


# Tests


class TestAuditWorkflow:
    def test_audit_creates_plan_and_artifacts(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")

        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0

        # Plan was created
        plan_path = project_dir / ".ossature" / "plan.toml"
        assert plan_path.exists()

        # Graph was created
        graph_path = project_dir / ".ossature" / "graph.toml"
        assert graph_path.exists()

        # Manifest was created
        manifest_path = project_dir / ".ossature" / "manifest.toml"
        assert manifest_path.exists()

        # Audit report was created
        report_path = project_dir / ".ossature" / "audit-report.md"
        assert report_path.exists()

        # Briefs were created
        assert (project_dir / ".ossature" / "context" / "project-brief.md").exists()
        assert (project_dir / ".ossature" / "context" / "spec-briefs" / "AUTH.md").exists()

        # Interface was created
        assert (project_dir / ".ossature" / "context" / "interfaces" / "AUTH.md").exists()

        # Plan has expected tasks
        from ossature.audit.planner import load_plan

        plan = load_plan(plan_path)
        assert plan is not None
        assert plan.meta.total_tasks == 3
        assert all(t.spec == "AUTH" for t in plan.tasks)
        assert all(t.status == TaskStatus.PENDING for t in plan.tasks)

    def test_audit_multi_spec_creates_correct_plan(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")

        with patch_all_agents({"AUTH": AUTH_PLAN, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0

        from ossature.audit.planner import load_plan

        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        assert plan.meta.total_tasks == 5
        assert plan.meta.specs == ["AUTH", "API"]

        # AUTH tasks first, then API
        auth_tasks = [t for t in plan.tasks if t.spec == "AUTH"]
        api_tasks = [t for t in plan.tasks if t.spec == "API"]
        assert len(auth_tasks) == 3
        assert len(api_tasks) == 2

        # First API task depends on last AUTH task
        assert auth_tasks[-1].id in api_tasks[0].depends_on


class TestIncrementalReplan:
    def _run_initial_audit(
        self, runner: CliRunner, project_dir: Path, spec_plans: dict[str, SpecTaskPlan]
    ):
        with patch_all_agents(spec_plans):
            result = run_in_project(runner, project_dir, ["audit"])
        assert result.exit_code == 0
        return result

    def _mark_tasks_done(self, project_dir: Path):
        """Simulate a completed build by marking all tasks as done."""
        from ossature.audit.planner import load_plan, write_plan

        plan_path = project_dir / ".ossature" / "plan.toml"
        plan = load_plan(plan_path)
        assert plan is not None
        for task in plan.tasks:
            task.status = TaskStatus.DONE
        write_plan(plan, plan_path)
        return plan

    def _write_fake_state(self, project_dir: Path, plan):
        """Write fake state.toml entries for all tasks."""
        from ossature.build.state import BuildState, TaskState, write_state

        state = BuildState()
        for task in plan.tasks:
            state.set(
                task.id,
                TaskState(
                    input_hash=f"sha256:input-{task.id}",
                    output_hash=f"sha256:output-{task.id}",
                    created_files=list(task.outputs),
                ),
            )
        write_state(state, project_dir / ".ossature" / "state.toml")

    def _write_fake_task_dirs(self, project_dir: Path, plan):
        """Create task directories like a real build would."""
        tasks_dir = project_dir / ".ossature" / "tasks"
        for task in plan.tasks:
            slug = (
                task.title.lower().replace(" ", "-").replace(":", "").replace("/", "-").strip("-")
            )
            task_dir = tasks_dir / f"{task.id}-{slug}"
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "prompt.md").write_text(f"Prompt for {task.title}")
            (task_dir / "response.md").write_text(f"Response for {task.title}")

    def test_incremental_replan_preserves_unchanged_spec_status(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        # Setup: two specs, initial audit
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        # Simulate completed build
        plan = self._mark_tasks_done(project_dir)
        self._write_fake_state(project_dir, plan)

        # Edit AUTH spec to trigger re-audit
        write_smd(
            project_dir,
            "AUTH",
            "Authentication Module",
            overview="Updated overview with new requirements.",
        )

        # Run audit again — should only re-plan AUTH
        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        assert "Incremental re-plan" in result.output

        # Load the new plan
        from ossature.audit.planner import load_plan

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None

        # AUTH tasks are pending (re-planned), API tasks preserved as done
        auth_tasks = [t for t in new_plan.tasks if t.spec == "AUTH"]
        api_tasks = [t for t in new_plan.tasks if t.spec == "API"]

        assert len(auth_tasks) == 4  # AUTH_PLAN_V2 has 4 tasks
        assert len(api_tasks) == 2  # API unchanged
        assert all(t.status == TaskStatus.PENDING for t in auth_tasks)
        assert all(t.status == TaskStatus.DONE for t in api_tasks)

    def test_incremental_replan_renumbers_sequentially(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        plan = self._mark_tasks_done(project_dir)
        self._write_fake_state(project_dir, plan)

        write_smd(project_dir, "AUTH", "Authentication Module", overview="Changed overview.")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0

        from ossature.audit.planner import load_plan

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None

        # IDs are sequential: 001-006
        expected_ids = [f"{i:03d}" for i in range(1, new_plan.meta.total_tasks + 1)]
        assert [t.id for t in new_plan.tasks] == expected_ids

    def test_incremental_replan_remaps_state(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        plan = self._mark_tasks_done(project_dir)
        self._write_fake_state(project_dir, plan)

        write_smd(project_dir, "AUTH", "Authentication Module", overview="Changed overview.")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0

        # State.toml should have remapped API task IDs
        from ossature.build.state import load_state

        state = load_state(project_dir / ".ossature" / "state.toml")

        # Old AUTH task IDs should be gone
        for task_id in ["001", "002", "003"]:
            assert state.get(task_id) is None

        # API tasks should exist under new IDs
        from ossature.audit.planner import load_plan

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        new_api_ids = [t.id for t in new_plan.tasks if t.spec == "API"]
        for task_id in new_api_ids:
            assert state.get(task_id) is not None

    def test_incremental_replan_remaps_task_directories(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        plan = self._mark_tasks_done(project_dir)
        self._write_fake_state(project_dir, plan)
        self._write_fake_task_dirs(project_dir, plan)

        write_smd(project_dir, "AUTH", "Authentication Module", overview="Changed overview.")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0

        tasks_dir = project_dir / ".ossature" / "tasks"
        remaining_dirs = sorted(d.name for d in tasks_dir.iterdir() if d.is_dir())

        # Old AUTH task dirs (001, 002, 003) should be gone
        assert not any(d.startswith("001-auth") for d in remaining_dirs)
        assert not any(d.startswith("002-auth") for d in remaining_dirs)
        assert not any(d.startswith("003-auth") for d in remaining_dirs)

        # API task dirs should exist with new IDs and preserved content
        from ossature.audit.planner import load_plan

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        api_tasks = [t for t in new_plan.tasks if t.spec == "API"]
        for task in api_tasks:
            slug = (
                task.title.lower().replace(" ", "-").replace(":", "").replace("/", "-").strip("-")
            )
            task_dir = tasks_dir / f"{task.id}-{slug}"
            assert task_dir.exists(), f"Expected dir {task_dir.name} to exist"
            assert (task_dir / "prompt.md").exists()

    def test_full_replan_when_all_specs_changed(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        self._mark_tasks_done(project_dir)

        # Edit both specs
        write_smd(project_dir, "AUTH", "Auth Module v2", overview="New auth.")
        write_smd(project_dir, "API", "API Module v2", overview="New API.", depends="AUTH")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        # When all specs change, no incremental merge
        assert "Incremental re-plan" not in result.output

        from ossature.audit.planner import load_plan

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None
        # All tasks should be pending (full re-plan)
        assert all(t.status == TaskStatus.PENDING for t in new_plan.tasks)

    def test_replan_flag_forces_full_regen(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        self._mark_tasks_done(project_dir)

        # Edit only AUTH
        write_smd(project_dir, "AUTH", "Auth Module v2", overview="New auth.")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit", "--replan"])

        assert result.exit_code == 0
        assert "Incremental re-plan" not in result.output

        from ossature.audit.planner import load_plan

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None
        assert all(t.status == TaskStatus.PENDING for t in new_plan.tasks)

    def test_incremental_replan_removes_orphaned_output_files(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        plan = self._mark_tasks_done(project_dir)
        self._write_fake_state(project_dir, plan)
        self._write_fake_task_dirs(project_dir, plan)

        # Create fake output files as if a build had run
        output_dir = project_dir / "output"
        for task in plan.tasks:
            for filepath in task.outputs:
                full_path = output_dir / filepath
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(f"# {filepath}")

        # AUTH_PLAN has outputs: src/auth/__init__.py, src/auth/models.py, src/auth/service.py
        # AUTH_PLAN_V2 has: src/auth/__init__.py, src/auth/tokens.py, src/auth/service.py
        # So src/auth/models.py should be orphaned and removed
        assert (output_dir / "src/auth/models.py").exists()

        write_smd(project_dir, "AUTH", "Authentication Module", overview="Changed overview.")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        assert "Removed stale output" in result.output

        # Orphaned file should be gone
        assert not (output_dir / "src/auth/models.py").exists()

        # Non-orphaned files should still exist
        assert (output_dir / "src/auth/__init__.py").exists()
        assert (output_dir / "src/api/__init__.py").exists()
        assert (output_dir / "src/api/routes.py").exists()
