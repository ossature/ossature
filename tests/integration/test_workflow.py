from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from helpers import (
    _mock_agent_init,
    make_spec_task_plan,
    patch_all_agents,
    run_in_project,
    write_smd,
)
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.usage import RunUsage

from ossature.audit.planner import load_plan, write_plan
from ossature.build.state import BuildState, TaskState, load_state, write_state
from ossature.models.audit import (
    AuditFinding,
    CrossSpecAuditReport,
    Severity,
    SpecAuditReport,
)
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

        # LLM usage summary was printed
        assert "LLM usage:" in result.output

        # Plan has expected tasks
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
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan = load_plan(plan_path)
        assert plan is not None
        for task in plan.tasks:
            task.status = TaskStatus.DONE
        write_plan(plan, plan_path)
        return plan

    def _write_fake_state(self, project_dir: Path, plan):
        """Write fake state.toml entries for all tasks."""
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
        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None

        # AUTH tasks: matched outputs carry over DONE, new tasks are PENDING
        auth_tasks = [t for t in new_plan.tasks if t.spec == "AUTH"]
        api_tasks = [t for t in new_plan.tasks if t.spec == "API"]

        assert len(auth_tasks) == 4  # AUTH_PLAN_V2 has 4 tasks
        assert len(api_tasks) == 2  # API unchanged

        # __init__.py and service.py match old outputs → DONE
        # tokens.py and test_auth.py are new → PENDING
        auth_by_output = {t.outputs[0]: t.status for t in auth_tasks}
        assert auth_by_output["src/auth/__init__.py"] == TaskStatus.DONE
        assert auth_by_output["src/auth/service.py"] == TaskStatus.DONE
        assert auth_by_output["src/auth/tokens.py"] == TaskStatus.PENDING
        assert auth_by_output["tests/test_auth.py"] == TaskStatus.PENDING

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

        # State.toml should have remapped task IDs
        state = load_state(project_dir / ".ossature" / "state.toml")
        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")

        # Matched AUTH tasks (same outputs) keep state under new IDs
        # Unmatched AUTH tasks (old 002/models.py) have no state
        for task in new_plan.tasks:
            if task.spec == "AUTH" and task.status == TaskStatus.DONE:
                assert state.get(task.id) is not None
            elif task.spec == "AUTH" and task.status == TaskStatus.PENDING:
                assert state.get(task.id) is None

        # API tasks should exist under new IDs
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

        # Unmatched AUTH task dir (002/models.py) should be gone
        assert not any(d.startswith("002-auth") for d in remaining_dirs)

        # Matched AUTH task dirs (001/__init__.py, 003/service.py) are preserved
        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        matched_auth = [
            t for t in new_plan.tasks if t.spec == "AUTH" and t.status == TaskStatus.DONE
        ]
        assert len(matched_auth) == 2

        # API task dirs should exist with new IDs and preserved content
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
        """When every spec changes, incremental re-plan still runs per-spec
        and carries over status for tasks whose outputs match."""
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
        # Incremental re-plan applies even when all specs changed
        assert "Incremental re-plan" in result.output

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None
        # Tasks with matching outputs across versions preserve their DONE status
        done_outputs = {tuple(t.outputs) for t in new_plan.tasks if t.status == TaskStatus.DONE}
        # AUTH __init__.py and service.py are in both AUTH_PLAN and AUTH_PLAN_V2
        assert ("src/auth/__init__.py",) in done_outputs
        assert ("src/auth/service.py",) in done_outputs

    def test_single_spec_project_replan_is_incremental(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """Regression: single-spec projects should still go through the
        incremental path when the only spec changes."""
        write_smd(project_dir, "AUTH", "Authentication Module")
        self._run_initial_audit(runner, project_dir, {"AUTH": AUTH_PLAN})

        self._mark_tasks_done(project_dir)

        # Edit the only spec
        write_smd(project_dir, "AUTH", "Auth Module v2", overview="New auth.")

        with patch_all_agents({"AUTH": AUTH_PLAN_V2}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0
        assert "Incremental re-plan" in result.output

        new_plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert new_plan is not None
        # Tasks with matching outputs preserve their DONE status
        done_outputs = {tuple(t.outputs) for t in new_plan.tasks if t.status == TaskStatus.DONE}
        assert ("src/auth/__init__.py",) in done_outputs
        assert ("src/auth/service.py",) in done_outputs

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


class TestAuditIdempotency:
    def test_rerun_audit_unchanged_skips_reaudit(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")

        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result1 = run_in_project(runner, project_dir, ["audit"])
        assert result1.exit_code == 0

        # Second run — nothing changed
        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result2 = run_in_project(runner, project_dir, ["audit"])

        assert result2.exit_code == 0
        assert "No changes detected" in result2.output
        assert "Plan regeneration not required" in result2.output

    def test_audit_no_fix_reports_errors_without_fixing(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")

        error_finding = AuditFinding(
            severity=Severity.ERROR,
            location="Requirements",
            issue="Vague requirement",
            suggestion="Be more specific",
        )

        with patch_all_agents({"AUTH": AUTH_PLAN}, audit_findings=[error_finding]):
            result = run_in_project(runner, project_dir, ["audit", "--no-fix", "--errors-ok"])

        assert result.exit_code == 0
        # Error was reported but fixer was not invoked
        assert "1 error(s)" in result.output

    def test_audit_exits_nonzero_on_unresolved_errors(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")

        error_finding = AuditFinding(
            severity=Severity.ERROR,
            location="Requirements",
            issue="Missing spec",
            suggestion="Add it",
        )

        with patch_all_agents({"AUTH": AUTH_PLAN}, audit_findings=[error_finding]):
            result = run_in_project(runner, project_dir, ["audit", "--no-fix"])

        assert result.exit_code == 1
        assert "unresolved error(s)" in result.output

    def test_audit_three_spec_chain_preserves_topo_order(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        write_smd(project_dir, "DB", "Database Module", depends="API")

        with patch_all_agents({"AUTH": AUTH_PLAN, "API": API_PLAN, "DB": DB_PLAN}):
            result = run_in_project(runner, project_dir, ["audit"])

        assert result.exit_code == 0

        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        assert plan.meta.specs == ["AUTH", "API", "DB"]
        assert plan.meta.total_tasks == 7

        # Tasks are ordered: AUTH first, then API, then DB
        spec_order = []
        for t in plan.tasks:
            if not spec_order or spec_order[-1] != t.spec:
                spec_order.append(t.spec)
        assert spec_order == ["AUTH", "API", "DB"]

        # DB's first task depends on API's last task
        api_tasks = [t for t in plan.tasks if t.spec == "API"]
        db_tasks = [t for t in plan.tasks if t.spec == "DB"]
        assert api_tasks[-1].id in db_tasks[0].depends_on


class TestAuditFixerTracking:
    def test_audit_with_fixable_errors_exercises_fixer_tracker(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")

        error_finding = AuditFinding(
            severity=Severity.ERROR,
            location="Requirements > Core Requirement",
            issue="Missing error handling specification",
            suggestion="Add error cases for invalid input",
        )

        with patch_all_agents({"AUTH": AUTH_PLAN}, audit_findings=[error_finding]):
            result = run_in_project(runner, project_dir, ["audit", "--errors-ok"])

        assert result.exit_code == 0
        # Fixer was invoked (first audit returns error, triggers fix, re-audit is clean)
        assert "LLM usage:" in result.output


class TestBuildWorkflow:
    def _run_audit(self, runner: CliRunner, project_dir: Path, spec_plans: dict[str, SpecTaskPlan]):
        with patch_all_agents(spec_plans):
            result = run_in_project(runner, project_dir, ["audit"])
        assert result.exit_code == 0
        return result

    def _create_output_files(self, project_dir: Path, spec_plans: dict[str, SpecTaskPlan]):
        """Create fake output files so extract_spec_interface can read them."""
        output_dir = project_dir / "output"
        for plan in spec_plans.values():
            for task in plan.tasks:
                for filepath in task.outputs:
                    full_path = output_dir / filepath
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(f"# {filepath}\n")

    def test_build_auto_exercises_tracker(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN})

        # Pre-create output files so extract_spec_interface finds them
        self._create_output_files(project_dir, {"AUTH": AUTH_PLAN})

        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        assert "Build Complete" in result.output
        # LLM usage is shown in the build summary panel
        assert "LLM:" in result.output
        # Interface extraction ran for completed spec
        assert "Extracting interface for AUTH" in result.output

    def test_build_multi_spec_accumulates_usage(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        with patch_all_agents({"AUTH": AUTH_PLAN, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        assert "Build Complete" in result.output
        assert "LLM:" in result.output

    def test_build_marks_tasks_done_and_writes_state(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN})

        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0

        # Plan tasks should be marked DONE
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        assert all(t.status == TaskStatus.DONE for t in plan.tasks)

        # State file should exist with entries for all tasks
        state = load_state(project_dir / ".ossature" / "state.toml")
        for task in plan.tasks:
            stored = state.get(task.id)
            assert stored is not None
            assert stored.input_hash.startswith("sha256:")
            assert stored.output_hash.startswith("sha256:")

        # Task directories should have prompt and response files
        tasks_dir = project_dir / ".ossature" / "tasks"
        for task in plan.tasks:
            dirs = list(tasks_dir.glob(f"{task.id}-*"))
            assert len(dirs) == 1
            assert (dirs[0] / "prompt.md").exists()
            assert (dirs[0] / "response.md").exists()

    def test_build_already_done_skips(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN})

        # First build
        with patch_all_agents({"AUTH": AUTH_PLAN}):
            run_in_project(runner, project_dir, ["build", "--auto"])

        # Second build — all tasks already done
        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        assert "All tasks already completed" in result.output

    def test_build_force_rebuilds_completed_tasks(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN})

        # First build
        with patch_all_agents({"AUTH": AUTH_PLAN}):
            run_in_project(runner, project_dir, ["build", "--auto"])

        # Force rebuild — should re-run everything
        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto", "--force"])

        assert result.exit_code == 0
        assert "Build Complete" in result.output
        assert "3 pending" in result.output

    def test_build_resumes_from_partial(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        # Simulate partial build: mark AUTH tasks as done
        plan_path = project_dir / ".ossature" / "plan.toml"
        plan = load_plan(plan_path)
        assert plan is not None
        for task in plan.tasks:
            if task.spec == "AUTH":
                task.status = TaskStatus.DONE
        write_plan(plan, plan_path)

        # Resume build — should only run API tasks (2 pending)
        with patch_all_agents({"AUTH": AUTH_PLAN, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        assert "Build Complete" in result.output
        assert "2 pending" in result.output

    def test_build_spec_filter(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")
        self._run_audit(runner, project_dir, {"AUTH": AUTH_PLAN, "API": API_PLAN})

        # Build only AUTH spec
        with patch_all_agents({"AUTH": AUTH_PLAN, "API": API_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto", "--spec", "auth"])

        assert result.exit_code == 0
        assert "Build Complete" in result.output
        assert "spec: AUTH" in result.output

        # AUTH tasks should be done, API tasks should be skipped
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        auth_tasks = [t for t in plan.tasks if t.spec == "AUTH"]
        api_tasks = [t for t in plan.tasks if t.spec == "API"]
        assert all(t.status == TaskStatus.DONE for t in auth_tasks)
        assert all(t.status == TaskStatus.SKIPPED for t in api_tasks)

    def test_build_without_plan_fails(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "AUTH", "Authentication Module")
        # Don't run audit — no plan exists

        with patch_all_agents({"AUTH": AUTH_PLAN}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 1
        assert "No plan found" in result.output

    def test_build_copy_task_copies_files_without_llm(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        write_smd(project_dir, "ASSETS", "Assets Module")
        (project_dir / "context" / "logo.txt").write_text("LOGO DATA")
        copy_plan = make_spec_task_plan(
            [
                {
                    "title": "Copy logo",
                    "outputs": ["assets/logo.txt"],
                    "source": ["context://logo.txt"],
                },
                {"title": "App code", "outputs": ["src/app.py"], "depends_on": [1]},
            ]
        )
        self._run_audit(runner, project_dir, {"ASSETS": copy_plan})

        with patch_all_agents({"ASSETS": copy_plan}):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        copied = project_dir / "output" / "assets" / "logo.txt"
        assert copied.exists()
        assert copied.read_text() == "LOGO DATA"

        plan_path = project_dir / ".ossature" / "plan.toml"
        plan = load_plan(plan_path)
        assert plan is not None
        assert all(t.status == TaskStatus.DONE for t in plan.tasks)

        # Flip the second task back to pending and rebuild. execute_build now
        # iterates the already-DONE copy task, exercising its done-path branch.
        plan.tasks[1].status = TaskStatus.PENDING
        write_plan(plan, plan_path)

        with patch_all_agents({"ASSETS": copy_plan}):
            result2 = run_in_project(runner, project_dir, ["build", "--auto"])
        assert result2.exit_code == 0
        # Copy output untouched, copy task still done
        assert copied.read_text() == "LOGO DATA"
        plan2 = load_plan(plan_path)
        assert plan2.tasks[0].status == TaskStatus.DONE


def _interface_run_sync(spec_plans: dict[str, SpecTaskPlan], on_interface):
    """Build a mock Agent.run_sync that lets a callback drive build-time
    interface extraction per spec.

    Everything else behaves like the constant mock in helpers: planner returns
    the canned plan, audits return no findings, and briefs / the audit-time
    interface inference / the fixer return a constant string. The build's
    interface extraction is the only prompt that starts with "# Source files
    for", so it is the one routed through on_interface(spec_id, call_count).
    The call counter persists for the life of the returned function, so reusing
    the same function across two builds lets a test vary (or fail) the second
    extraction. on_interface may return a string or raise to simulate a failed
    extraction.
    """
    usage = RunUsage(input_tokens=0, output_tokens=0, requests=1)
    extract_counts: dict[str, int] = {}

    def run_sync(self, prompt, *args, **kwargs):
        result = MagicMock()
        result.usage = usage

        output_type = getattr(self, "_output_type", None)
        if output_type is SpecTaskPlan:
            for spec_id, plan in spec_plans.items():
                if f"id: {spec_id}" in prompt:
                    result.output = plan
                    return result
            result.output = next(iter(spec_plans.values()))
            return result
        if output_type is SpecAuditReport:
            result.output = SpecAuditReport(findings=[])
            return result
        if output_type is CrossSpecAuditReport:
            result.output = CrossSpecAuditReport(findings=[])
            return result

        if prompt.startswith("# Source files for "):
            spec_id = prompt.splitlines()[0].removeprefix("# Source files for ").strip()
            extract_counts[spec_id] = extract_counts.get(spec_id, 0) + 1
            result.output = on_interface(spec_id, extract_counts[spec_id])
            return result

        result.output = "Mock brief or interface content."
        return result

    return run_sync


def _patch_run_sync(run_sync) -> ExitStack:
    """Patch Agent.__init__ (to record output_type) and Agent.run_sync.

    Same wiring as helpers.patch_all_agents, but takes a pre-built run_sync so
    a single counter survives across multiple builds in one test.
    """
    stack = ExitStack()
    stack.enter_context(patch("pydantic_ai.Agent.__init__", _mock_agent_init))
    stack.enter_context(patch("pydantic_ai.Agent.run_sync", run_sync))
    return stack


class TestCrossSpecInvalidation:
    """Within-build cross-spec invalidation (issue #68).

    AUTH and API where API depends on AUTH. When AUTH rebuilds during a build,
    its dependent API should rebuild only if the AUTH interface API consumes
    actually changed. This is the same rule the across-build path already
    follows.
    """

    def _create_output_files(self, project_dir: Path, spec_plans: dict[str, SpecTaskPlan]):
        """Pre-create output files so extract_spec_interface has sources to read."""
        output_dir = project_dir / "output"
        for plan in spec_plans.values():
            for task in plan.tasks:
                for filepath in task.outputs:
                    full_path = output_dir / filepath
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(f"# {filepath}\n")

    def _write_specs(self, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")

    def _last_task(self, plan, spec: str):
        return [t for t in plan.tasks if t.spec == spec][-1]

    def _tamper_input_hash(self, project_dir: Path, task_id: str):
        """Corrupt the stored input hash of one task so it rebuilds next run."""
        state_path = project_dir / ".ossature" / "state.toml"
        state = load_state(state_path)
        stored = state.get(task_id)
        assert stored is not None
        stored.input_hash = "sha256:tampered"
        state.set(task_id, stored)
        write_state(state, state_path)

    def test_dependent_not_rebuilt_when_interface_unchanged(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """The #68 fix: AUTH rebuilds but its interface is unchanged, so the
        cross-spec dependent (API: Scaffold) is skipped rather than cascaded.
        """
        self._write_specs(project_dir)
        plans = {"AUTH": AUTH_PLAN, "API": API_PLAN}

        with patch_all_agents(plans):
            assert run_in_project(runner, project_dir, ["audit"]).exit_code == 0
        self._create_output_files(project_dir, plans)
        with patch_all_agents(plans):
            assert run_in_project(runner, project_dir, ["build", "--auto"]).exit_code == 0

        # Force AUTH's last task to rebuild by corrupting its stored input hash.
        # Flip API's last task back to pending so the second build does not
        # short-circuit on "all tasks already completed". Tampering AUTH's last
        # task (not an earlier one) avoids a same-spec cascade inside AUTH that
        # would otherwise produce a "dependency rebuilt" line and muddy the
        # cross-spec assertion below.
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        self._tamper_input_hash(project_dir, self._last_task(plan, "AUTH").id)
        self._last_task(plan, "API").status = TaskStatus.PENDING
        write_plan(plan, project_dir / ".ossature" / "plan.toml")

        with patch_all_agents(plans):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        # AUTH genuinely rebuilt this run (keeps the test non-vacuous).
        assert "input changed, re-running" in result.output
        # The dependent's skip is the consequence of a successful, unchanged
        # interface extraction for AUTH.
        assert "Extracting interface for AUTH" in result.output
        # AUTH rebuilding did not force the cross-spec dependent to rebuild.
        assert "dependency rebuilt" not in result.output
        # API: Scaffold carries the cross-spec edge to AUTH and is skipped.
        assert "API: Scaffold (done)" in result.output

    def test_dependent_rebuilt_when_interface_changes(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """When AUTH's extracted interface actually changes, the dependent's
        input hash moves and API: Scaffold re-runs through the input-changed
        path.
        """
        self._write_specs(project_dir)
        plans = {"AUTH": AUTH_PLAN, "API": API_PLAN}
        run_sync = _interface_run_sync(plans, lambda spec_id, n: f"interface for {spec_id} v{n}")

        with _patch_run_sync(run_sync):
            assert run_in_project(runner, project_dir, ["audit"]).exit_code == 0
        self._create_output_files(project_dir, plans)
        with _patch_run_sync(run_sync):
            assert run_in_project(runner, project_dir, ["build", "--auto"]).exit_code == 0

        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        scaffold = next(t for t in plan.tasks if t.title == "API: Scaffold")
        before = load_state(project_dir / ".ossature" / "state.toml").get(scaffold.id)
        assert before is not None
        input_hash_before = before.input_hash

        # Flip AUTH's last task to pending: this forces AUTH to rebuild and also
        # gets the build past the "all completed" short-circuit. AUTH's fresh
        # extraction returns different text (v2), so the dependent's input hash
        # must move.
        self._last_task(plan, "AUTH").status = TaskStatus.PENDING
        write_plan(plan, project_dir / ".ossature" / "plan.toml")

        with _patch_run_sync(run_sync):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        after = load_state(project_dir / ".ossature" / "state.toml").get(scaffold.id)
        assert after is not None
        # API: Scaffold re-ran because the AUTH interface in its prompt changed.
        # (API: Routes also cascades off API: Scaffold via the same-spec path,
        # so a "dependency rebuilt" line is expected here and not asserted.)
        assert after.input_hash != input_hash_before

    def test_no_change_rebuild_is_noop_across_builds(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """Across-build parity: a second build with nothing changed rebuilds
        nothing, so no dependent is ever cascaded.
        """
        self._write_specs(project_dir)
        plans = {"AUTH": AUTH_PLAN, "API": API_PLAN}

        with patch_all_agents(plans):
            assert run_in_project(runner, project_dir, ["audit"]).exit_code == 0
        self._create_output_files(project_dir, plans)
        with patch_all_agents(plans):
            assert run_in_project(runner, project_dir, ["build", "--auto"]).exit_code == 0
        with patch_all_agents(plans):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        assert "All tasks already completed" in result.output
        assert "dependency rebuilt" not in result.output

        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        assert all(t.status == TaskStatus.DONE for t in plan.tasks)

    def test_dependent_rebuilt_when_upstream_extraction_fails(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """Regression for the false-skip guard: if AUTH rebuilds but its
        interface extraction fails, the interface on disk is untrustworthy, so
        the dependent is forced to rebuild instead of skipping against stale
        bytes. Removing the interface_refreshed_specs gating reintroduces the
        false skip and breaks this test.
        """
        self._write_specs(project_dir)
        plans = {"AUTH": AUTH_PLAN, "API": API_PLAN}

        def on_interface(spec_id: str, n: int) -> str:
            # First extraction (build 1) succeeds; the second AUTH extraction
            # (build 2) fails.
            if spec_id == "AUTH" and n >= 2:
                raise AgentRunError("interface extraction boom")
            return f"interface for {spec_id}"

        run_sync = _interface_run_sync(plans, on_interface)

        with _patch_run_sync(run_sync):
            assert run_in_project(runner, project_dir, ["audit"]).exit_code == 0
        self._create_output_files(project_dir, plans)
        with _patch_run_sync(run_sync):
            assert run_in_project(runner, project_dir, ["build", "--auto"]).exit_code == 0

        # Tamper AUTH's last task so AUTH rebuilds; flip API's last task to
        # pending to get past the "all completed" short-circuit.
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        self._tamper_input_hash(project_dir, self._last_task(plan, "AUTH").id)
        self._last_task(plan, "API").status = TaskStatus.PENDING
        write_plan(plan, project_dir / ".ossature" / "plan.toml")

        with _patch_run_sync(run_sync):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        # The extraction failure was reported.
        assert "Interface extraction failed for AUTH" in result.output
        # API: Scaffold was forced to rebuild rather than skip against the stale
        # interface (cross_spec_stale keeps the conservative behavior).
        assert "dependency rebuilt" in result.output
        assert "API: Scaffold (done)" not in result.output

    def test_dependent_rebuilt_when_upstream_yields_no_extractable_source(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """Regression for the no-source false-skip: AUTH rebuilds but its
        outputs are gone (the same shape as an all-copy upstream, or outputs
        removed or renamed across a re-plan), so build-time extraction writes no
        interface. The AUTH interface file from build 1 is still on disk and is
        now stale. The dependent must rebuild instead of skipping against those
        stale bytes. Before extract_spec_interface signalled whether it wrote,
        AUTH was wrongly added to interface_refreshed_specs and API: Scaffold
        was falsely skipped against the stale interface.
        """
        self._write_specs(project_dir)
        plans = {"AUTH": AUTH_PLAN, "API": API_PLAN}

        with patch_all_agents(plans):
            assert run_in_project(runner, project_dir, ["audit"]).exit_code == 0
        self._create_output_files(project_dir, plans)
        with patch_all_agents(plans):
            assert run_in_project(runner, project_dir, ["build", "--auto"]).exit_code == 0

        # Build 1 wrote an AUTH interface from its source files.
        iface = project_dir / ".ossature" / "context" / "interfaces" / "AUTH.md"
        assert iface.exists()
        stale_bytes = iface.read_text()

        # Force AUTH to rebuild, flip API's last task to pending to get past the
        # "all completed" short-circuit, and remove AUTH's outputs so the second
        # extraction finds no source and writes nothing.
        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        self._tamper_input_hash(project_dir, self._last_task(plan, "AUTH").id)
        self._last_task(plan, "API").status = TaskStatus.PENDING
        write_plan(plan, project_dir / ".ossature" / "plan.toml")
        for task in plan.tasks:
            if task.spec == "AUTH":
                for filepath in task.outputs:
                    (project_dir / "output" / filepath).unlink()

        with patch_all_agents(plans):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        # AUTH genuinely rebuilt this run (keeps the test non-vacuous).
        assert "input changed, re-running" in result.output
        # No fresh interface was written, so the stale build-1 file is untouched.
        assert iface.read_text() == stale_bytes
        # API: Scaffold was forced to rebuild rather than skip against the stale
        # interface (cross_spec_stale fires because AUTH was not refreshed).
        assert "dependency rebuilt" in result.output
        assert "API: Scaffold (done)" not in result.output

    def test_dependent_rebuilds_on_equivalent_reworded_interface(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        """Honesty check for the residual from #15 / Part B: the input hash is
        over the raw interface text, so a semantically equivalent but reworded
        interface still moves the hash and rebuilds the dependent. There is no
        semantic interface diffing today; this documents that.
        """
        self._write_specs(project_dir)
        plans = {"AUTH": AUTH_PLAN, "API": API_PLAN}
        rewordings = {
            1: "def login(user, password): ...",
            2: "def login(username, pwd): ...",
        }

        def on_interface(spec_id: str, n: int) -> str:
            if spec_id == "AUTH":
                return rewordings.get(n, rewordings[2])
            return f"interface for {spec_id}"

        run_sync = _interface_run_sync(plans, on_interface)

        with _patch_run_sync(run_sync):
            assert run_in_project(runner, project_dir, ["audit"]).exit_code == 0
        self._create_output_files(project_dir, plans)
        with _patch_run_sync(run_sync):
            assert run_in_project(runner, project_dir, ["build", "--auto"]).exit_code == 0

        plan = load_plan(project_dir / ".ossature" / "plan.toml")
        assert plan is not None
        scaffold = next(t for t in plan.tasks if t.title == "API: Scaffold")
        before = load_state(project_dir / ".ossature" / "state.toml").get(scaffold.id)
        assert before is not None
        input_hash_before = before.input_hash

        # Tamper AUTH's last task so AUTH rebuilds; flip API's last task to
        # pending to get past the "all completed" short-circuit.
        self._tamper_input_hash(project_dir, self._last_task(plan, "AUTH").id)
        self._last_task(plan, "API").status = TaskStatus.PENDING
        write_plan(plan, project_dir / ".ossature" / "plan.toml")

        with _patch_run_sync(run_sync):
            result = run_in_project(runner, project_dir, ["build", "--auto"])

        assert result.exit_code == 0
        after = load_state(project_dir / ".ossature" / "state.toml").get(scaffold.id)
        assert after is not None
        # Reworded-but-equivalent interface still rebuilds the dependent.
        assert after.input_hash != input_hash_before
