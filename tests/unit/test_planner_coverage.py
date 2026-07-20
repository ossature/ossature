"""Coverage for planner edge branches surfaced by the planner/ package split."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import make_config, make_smd, make_task
from pydantic_ai import ModelRetry

from ossature.audit.graph import SpecGraph, SpecGraphEntry
from ossature.audit.planner import (
    incremental_merge_plan,
    merge_into_global_plan,
    write_plan,
)
from ossature.audit.planner.plan_io import remap_task_directories, remove_orphaned_output_files
from ossature.audit.planner.planning import generate_plan, validate_verify_commands
from ossature.models.plan import Plan, PlanMeta, PlannerTask, SpecTaskPlan, TaskStatus
from ossature.promptspec import resolve_profile


def _spec_plan(titles: list[str]) -> SpecTaskPlan:
    return SpecTaskPlan(
        tasks=[
            PlannerTask(
                title=t,
                description="",
                outputs=[f"{t}.py"],
                depends_on=[],
                spec_refs=[],
                arch_refs=[],
                verify="cargo check",
            )
            for t in titles
        ]
    )


class TestMergeSkipBranches:
    def test_fresh_merge_skips_spec_without_plan(self):
        # AUTH is in the graph level but absent from spec_plans -> skipped.
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="a.smd", depends=[])],
            levels=[["AUTH"]],
        )
        plan = merge_into_global_plan({}, graph, [make_smd("AUTH")])
        assert plan.tasks == []

    def test_incremental_skips_changed_spec_without_new_plan(self):
        existing = Plan(
            meta=PlanMeta(generated_at="t", total_tasks=1, specs=["AUTH"]),
            tasks=[make_task("001", "AUTH", outputs=["a.py"], status=TaskStatus.DONE)],
        )
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="a.smd", depends=[])],
            levels=[["AUTH"]],
        )
        plan, _, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={},  # AUTH is changed but has no fresh plan
            changed_spec_ids={"AUTH"},
            graph=graph,
            parsed_smds=[make_smd("AUTH")],
        )
        assert plan.tasks == []


class TestPlanIoBranches:
    def test_remap_ignores_non_directory_entries(self, tmp_path: Path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "stray.txt").write_text("not a task dir")
        old_plan = Plan(meta=PlanMeta(generated_at="t", total_tasks=0, specs=[]), tasks=[])
        # Must not raise on the stray file.
        remap_task_directories(tasks_dir, {}, set(), old_plan)
        assert (tasks_dir / "stray.txt").exists()

    def test_remove_orphaned_stops_at_nonempty_parent(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "keep.py").write_text("x")
        (tmp_path / "pkg" / "gone.py").write_text("y")
        removed = remove_orphaned_output_files(["pkg/gone.py"], tmp_path)
        assert removed == ["pkg/gone.py"]
        # keep.py holds the parent, so the rmdir stops (OSError branch).
        assert (tmp_path / "pkg").exists()
        assert (tmp_path / "pkg" / "keep.py").exists()

    def test_remove_orphaned_prunes_emptied_parents(self, tmp_path: Path):
        (tmp_path / "pkg" / "sub").mkdir(parents=True)
        (tmp_path / "pkg" / "sub" / "gone.py").write_text("y")
        removed = remove_orphaned_output_files(["pkg/sub/gone.py"], tmp_path)
        assert removed == ["pkg/sub/gone.py"]
        # Both now-empty parent dirs are pruned up to the output root.
        assert not (tmp_path / "pkg").exists()

    def test_write_plan_persists_optional_fields(self, tmp_path: Path):
        task = make_task("001", "AUDIO", outputs=["a.mp3"])
        task.source = ["assets/a.mp3"]
        task.notes = "copy verbatim"
        task.context_files = ["ref/spec.md"]
        task.cross_spec_interfaces = ["DB"]
        task.covers = ["primary-action"]
        task.inject_files = ["b.py"]
        plan = Plan(meta=PlanMeta(generated_at="t", total_tasks=1, specs=["AUDIO"]), tasks=[task])
        path = tmp_path / "plan.toml"
        write_plan(plan, path)
        content = path.read_text()
        assert "context://assets/a.mp3" in content
        assert "copy verbatim" in content
        assert "ref/spec.md" in content
        assert "primary-action" in content


class TestValidateVerifyCommands:
    def test_valid_plan_passes_through(self):
        profile = resolve_profile("rust")
        plan = _spec_plan(["Scaffold"])
        assert validate_verify_commands(plan, profile) is plan

    def test_invalid_plan_raises_model_retry(self):
        profile = resolve_profile("python")
        plan = _spec_plan(["Scaffold"])
        with (
            patch(
                "ossature.audit.planner.planning.check_verify_commands",
                return_value=["sentinel-error"],
            ),
            patch(
                "ossature.audit.planner.planning.format_validator_errors",
                return_value="wrong verify command",
            ),
            pytest.raises(ModelRetry, match="wrong verify command"),
        ):
            validate_verify_commands(plan, profile)


def _mock_spec_plan_result(titles: list[str]):
    return SimpleNamespace(output=_spec_plan(titles), usage=SimpleNamespace())


class TestGenerateSpecPlanSections:
    def test_framework_and_setup_reach_the_prompt(self, tmp_path: Path):
        config = make_config(tmp_path, language="rust")
        config.output.framework = "actix"
        config.build.setup = ["cargo build"]
        smd = make_smd("AUTH")
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="a.smd", depends=[])],
            levels=[["AUTH"]],
        )

        captured: dict[str, str] = {}

        def fake_run(agent, prompt, **kwargs):
            captured["prompt"] = prompt
            return _mock_spec_plan_result(["Scaffold"])

        with (
            patch("ossature.audit.planner.planning.Agent"),
            patch("ossature.audit.planner.planning.run_agent_sync", side_effect=fake_run),
        ):
            generate_plan(config, [smd], {}, graph, {})

        assert "Framework: actix" in captured["prompt"]
        assert "Build Setup Command" in captured["prompt"]


class TestGeneratePlanBranches:
    def test_skips_changed_spec_with_no_matching_smd(self, tmp_path: Path):
        config = make_config(tmp_path)
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="GHOST", file="g.smd", depends=[])],
            levels=[["GHOST"]],
        )
        # GHOST is in specs_to_replan but has no parsed SMD -> skipped, no agent call.
        with patch("ossature.audit.planner.planning.run_agent_sync") as run:
            plan, _, _ = generate_plan(config, [], {}, graph, {}, changed_spec_ids={"GHOST"})
        run.assert_not_called()
        assert plan.tasks == []

    def test_previous_tasks_empty_when_only_verify_tasks(self, tmp_path: Path):
        config = make_config(tmp_path, language="rust")
        smd = make_smd("AUTH")
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="a.smd", depends=[])],
            levels=[["AUTH"]],
        )
        verify_task = make_task("001", "AUTH", outputs=["checks/x.py"], status=TaskStatus.DONE)
        verify_task.kind = "verify"
        existing = Plan(
            meta=PlanMeta(generated_at="t", total_tasks=1, specs=["AUTH"]),
            tasks=[verify_task],
        )
        # Seed a snapshot so a diff is attempted; the only existing task is a
        # verify task, so previous_tasks resolves to empty then None.
        config.metadata_snapshots_path.mkdir(parents=True, exist_ok=True)
        (config.metadata_snapshots_path / "AUTH.md").write_text("old snapshot")

        with (
            patch("ossature.audit.planner.planning.Agent"),
            patch(
                "ossature.audit.planner.planning.run_agent_sync",
                side_effect=lambda *a, **k: _mock_spec_plan_result(["Scaffold"]),
            ),
        ):
            plan, _, _ = generate_plan(
                config,
                [smd],
                {},
                graph,
                {},
                changed_spec_ids={"AUTH"},
                existing_plan=existing,
            )
        assert any(t.spec == "AUTH" for t in plan.tasks)
