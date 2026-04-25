from pathlib import Path

from click.testing import CliRunner
from helpers import make_spec_task_plan, patch_all_agents, run_in_project, write_smd

from ossature.audit.planner import load_plan, write_plan
from ossature.build.state import BuildState, TaskState, load_state, write_state
from ossature.models.audit import AuditFinding, Severity
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
