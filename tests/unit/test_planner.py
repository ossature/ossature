from pathlib import Path

from conftest import make_smd, make_task

from ossature.audit.graph import SpecGraph, SpecGraphEntry
from ossature.audit.planner import (
    incremental_merge_plan,
    load_plan,
    merge_into_global_plan,
    remap_build_state,
    remap_task_directories,
    write_plan,
    write_task_definitions,
)
from ossature.build.state import BuildState, TaskState, load_state, write_state
from ossature.models.plan import Plan, PlanMeta, PlannerTask, PlanTask, SpecTaskPlan, TaskStatus


def _make_spec_plan(tasks: list[dict]) -> SpecTaskPlan:
    return SpecTaskPlan(
        tasks=[
            PlannerTask(
                title=t["title"],
                description=t.get("description", ""),
                outputs=t.get("outputs", []),
                depends_on=t.get("depends_on", []),
                spec_refs=t.get("spec_refs", []),
                arch_refs=t.get("arch_refs", []),
                verify=t.get("verify", "cargo check"),
            )
            for t in tasks
        ]
    )


class TestMergeIntoGlobalPlan:
    def test_single_spec_assigns_sequential_ids(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/auth/mod.rs"]},
                    {"title": "Types", "outputs": ["src/auth/types.rs"], "depends_on": [1]},
                    {"title": "Tests", "outputs": ["tests/auth.rs"], "depends_on": [2]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.meta.total_tasks == 3
        assert plan.meta.specs == ["AUTH"]
        assert [t.id for t in plan.tasks] == ["001", "002", "003"]

    def test_single_spec_remaps_local_depends_to_global(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/mod.rs"]},
                    {"title": "Types", "depends_on": [1]},
                    {"title": "Service", "depends_on": [1, 2]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.tasks[0].depends_on == []
        assert plan.tasks[1].depends_on == ["001"]
        assert plan.tasks[2].depends_on == ["001", "002"]

    def test_cross_spec_dependency_wiring(self):
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
                SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH"]),
            ],
            levels=[["AUTH"], ["API"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Auth Scaffold", "outputs": ["src/auth/mod.rs"]},
                    {"title": "Auth Tests", "outputs": ["tests/auth.rs"], "depends_on": [1]},
                ]
            ),
            "API": _make_spec_plan(
                [
                    {"title": "API Scaffold", "outputs": ["src/api/mod.rs"]},
                    {"title": "API Routes", "outputs": ["src/api/routes.rs"], "depends_on": [1]},
                ]
            ),
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert len(plan.tasks) == 4
        # First API task should depend on last AUTH task
        api_scaffold = plan.tasks[2]
        assert api_scaffold.spec == "API"
        assert "002" in api_scaffold.depends_on  # last AUTH task

    def test_cross_spec_interfaces_set_on_all_dependent_tasks(self):
        smds = [make_smd("AUTH"), make_smd("DB"), make_smd("API", depends=["AUTH", "DB"])]
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
                SpecGraphEntry(id="DB", file="specs/db.smd", depends=[]),
                SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH", "DB"]),
            ],
            levels=[["AUTH", "DB"], ["API"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan([{"title": "Auth Scaffold"}]),
            "DB": _make_spec_plan([{"title": "DB Scaffold"}]),
            "API": _make_spec_plan(
                [
                    {"title": "API Scaffold"},
                    {"title": "API Routes", "depends_on": [1]},
                    {"title": "API Tests", "depends_on": [2]},
                ]
            ),
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        api_tasks = [t for t in plan.tasks if t.spec == "API"]
        assert len(api_tasks) == 3
        for api_task in api_tasks:
            assert sorted(api_task.cross_spec_interfaces) == ["AUTH", "DB"]

    def test_spec_refs_prefixed_with_spec_id(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {
                        "title": "Scaffold",
                        "spec_refs": ["overview", "requirements"],
                        "arch_refs": ["dependencies"],
                    },
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.tasks[0].spec_refs == ["AUTH:overview", "AUTH:requirements"]
        assert plan.tasks[0].arch_refs == ["AUTH:dependencies"]

    def test_inject_files_from_same_spec_dependencies(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/auth/mod.rs"]},
                    {"title": "Types", "outputs": ["src/auth/types.rs"], "depends_on": [1]},
                    {
                        "title": "Service",
                        "outputs": ["src/auth/service.rs"],
                        "depends_on": [1, 2],
                    },
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.tasks[1].inject_files == ["src/auth/mod.rs"]
        assert plan.tasks[2].inject_files == ["src/auth/mod.rs", "src/auth/types.rs"]

    def test_no_inject_files_across_spec_boundaries(self):
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
                SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH"]),
            ],
            levels=[["AUTH"], ["API"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Auth Scaffold", "outputs": ["src/auth/mod.rs"]},
                ]
            ),
            "API": _make_spec_plan(
                [
                    {"title": "API Scaffold", "outputs": ["src/api/mod.rs"]},
                ]
            ),
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        api_task = next(t for t in plan.tasks if t.spec == "API")
        # Should NOT inject auth files — cross-spec uses interfaces, not inject_files
        assert api_task.inject_files == []

    def test_all_tasks_start_as_pending(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold"},
                    {"title": "Types", "depends_on": [1]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert all(t.status == TaskStatus.PENDING for t in plan.tasks)

    def test_empty_spec_plan_skipped(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {"AUTH": _make_spec_plan([])}

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.meta.total_tasks == 0
        assert plan.tasks == []


class TestPlanTomlRoundtrip:
    def test_write_and_load(self, temp_dir: Path):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/mod.rs"], "verify": "cargo check"},
                    {
                        "title": "Types",
                        "outputs": ["src/types.rs"],
                        "depends_on": [1],
                        "spec_refs": ["overview"],
                        "verify": "cargo check",
                    },
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)
        filepath = temp_dir / "plan.toml"

        write_plan(plan, filepath)

        assert filepath.exists()
        content = filepath.read_text()
        assert "Generated by `ossature audit`" in content

        loaded = load_plan(filepath)

        assert loaded is not None
        assert loaded.meta.total_tasks == plan.meta.total_tasks
        assert loaded.meta.specs == plan.meta.specs
        assert len(loaded.tasks) == len(plan.tasks)

        for orig, loaded_task in zip(plan.tasks, loaded.tasks):
            assert loaded_task.id == orig.id
            assert loaded_task.spec == orig.spec
            assert loaded_task.title == orig.title
            assert loaded_task.outputs == orig.outputs
            assert loaded_task.depends_on == orig.depends_on
            assert loaded_task.spec_refs == orig.spec_refs
            assert loaded_task.status == TaskStatus.PENDING
            assert loaded_task.verify == orig.verify

    def test_load_nonexistent_returns_none(self, temp_dir: Path):
        assert load_plan(temp_dir / "nonexistent.toml") is None

    def test_load_malformed_returns_none(self, temp_dir: Path):
        filepath = temp_dir / "bad.toml"
        filepath.write_text("not valid { toml [[[")
        assert load_plan(filepath) is None


class TestWriteTaskDefinitions:
    def test_creates_task_directories(self, temp_dir: Path):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/mod.rs"]},
                    {"title": "Types", "outputs": ["src/types.rs"], "depends_on": [1]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)
        tasks_dir = temp_dir / "tasks"

        write_task_definitions(plan, tasks_dir)

        task_dirs = sorted(tasks_dir.iterdir())
        assert len(task_dirs) == 2
        assert (task_dirs[0] / "task.toml").exists()
        assert (task_dirs[1] / "task.toml").exists()


def _make_existing_plan(tasks: list[PlanTask]) -> Plan:
    specs = sorted({t.spec for t in tasks})
    return Plan(
        meta=PlanMeta(
            generated_at="2026-01-01T00:00:00Z",
            total_tasks=len(tasks),
            specs=specs,
        ),
        tasks=tasks,
    )


def _two_spec_graph() -> SpecGraph:
    return SpecGraph(
        specs=[
            SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
            SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH"]),
        ],
        levels=[["AUTH"], ["API"]],
    )


def _three_spec_graph() -> SpecGraph:
    return SpecGraph(
        specs=[
            SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
            SpecGraphEntry(id="DB", file="specs/db.smd", depends=[]),
            SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH", "DB"]),
        ],
        levels=[["AUTH", "DB"], ["API"]],
    )


class TestIncrementalMergePlan:
    def test_preserves_unchanged_spec_tasks_with_status(self):
        """Unchanged spec tasks keep their done/failed/etc status."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth/mod.rs"], status=TaskStatus.DONE),
                make_task(
                    "002",
                    "AUTH",
                    outputs=["src/auth/types.rs"],
                    depends_on=["001"],
                    status=TaskStatus.DONE,
                ),
                make_task(
                    "003",
                    "API",
                    outputs=["src/api/mod.rs"],
                    depends_on=["002"],
                    status=TaskStatus.DONE,
                ),
                make_task(
                    "004",
                    "API",
                    outputs=["src/api/routes.rs"],
                    depends_on=["003"],
                    status=TaskStatus.DONE,
                ),
            ]
        )
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = _two_spec_graph()

        # Re-plan only AUTH with new tasks
        new_auth_plan = _make_spec_plan(
            [
                {"title": "Auth Scaffold v2", "outputs": ["src/auth/mod.rs"]},
                {"title": "Auth Tokens v2", "outputs": ["src/auth/tokens.rs"], "depends_on": [1]},
                {"title": "Auth Tests v2", "outputs": ["tests/auth.rs"], "depends_on": [2]},
            ]
        )

        plan, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={"AUTH": new_auth_plan},
            changed_spec_ids={"AUTH"},
            graph=graph,
            parsed_smds=smds,
        )

        # 3 new AUTH tasks + 2 preserved API tasks
        assert plan.meta.total_tasks == 5
        assert [t.id for t in plan.tasks] == ["001", "002", "003", "004", "005"]

        # AUTH tasks are pending (freshly planned)
        auth_tasks = [t for t in plan.tasks if t.spec == "AUTH"]
        assert all(t.status == TaskStatus.PENDING for t in auth_tasks)

        # API tasks preserved their done status
        api_tasks = [t for t in plan.tasks if t.spec == "API"]
        assert all(t.status == TaskStatus.DONE for t in api_tasks)

    def test_renumbers_sequentially(self):
        """All tasks get clean sequential IDs regardless of which spec changed."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", outputs=["src/auth/mod.rs"], status=TaskStatus.DONE),
                make_task("002", "DB", outputs=["src/db/mod.rs"], status=TaskStatus.DONE),
                make_task(
                    "003",
                    "API",
                    outputs=["src/api/mod.rs"],
                    depends_on=["001", "002"],
                    status=TaskStatus.DONE,
                ),
            ]
        )
        smds = [make_smd("AUTH"), make_smd("DB"), make_smd("API", depends=["AUTH", "DB"])]
        graph = _three_spec_graph()

        # Re-plan only DB
        new_db_plan = _make_spec_plan(
            [
                {"title": "DB Scaffold v2", "outputs": ["src/db/mod.rs"]},
                {"title": "DB Models v2", "outputs": ["src/db/models.rs"], "depends_on": [1]},
            ]
        )

        plan, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={"DB": new_db_plan},
            changed_spec_ids={"DB"},
            graph=graph,
            parsed_smds=smds,
        )

        assert [t.id for t in plan.tasks] == ["001", "002", "003", "004"]
        assert plan.tasks[0].spec == "AUTH"  # preserved
        assert plan.tasks[1].spec == "DB"  # new
        assert plan.tasks[2].spec == "DB"  # new
        assert plan.tasks[3].spec == "API"  # preserved

    def test_id_remap_returned(self):
        """The id_remap maps old preserved task IDs to new IDs."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", outputs=["a.rs"], status=TaskStatus.DONE),
                make_task("002", "DB", outputs=["b.rs"], status=TaskStatus.DONE),
                make_task(
                    "003",
                    "API",
                    outputs=["c.rs"],
                    depends_on=["001", "002"],
                    status=TaskStatus.DONE,
                ),
            ]
        )
        smds = [make_smd("AUTH"), make_smd("DB"), make_smd("API", depends=["AUTH", "DB"])]
        graph = _three_spec_graph()

        # Re-plan AUTH (first spec) — DB and API should remap
        new_auth_plan = _make_spec_plan(
            [
                {"title": "Auth v2", "outputs": ["a.rs"]},
                {"title": "Auth v2 extra", "outputs": ["a2.rs"], "depends_on": [1]},
            ]
        )

        _, id_remap = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={"AUTH": new_auth_plan},
            changed_spec_ids={"AUTH"},
            graph=graph,
            parsed_smds=smds,
        )

        # DB old 002 -> new 003, API old 003 -> new 004
        assert id_remap["002"] == "003"
        assert id_remap["003"] == "004"
        # AUTH tasks are new, not in remap
        assert "001" not in id_remap

    def test_depends_on_remapped_for_preserved_tasks(self):
        """Preserved tasks have their depends_on updated to use new IDs."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", outputs=["a.rs"], status=TaskStatus.DONE),
                make_task(
                    "002", "AUTH", outputs=["b.rs"], depends_on=["001"], status=TaskStatus.DONE
                ),
                make_task(
                    "003", "API", outputs=["c.rs"], depends_on=["002"], status=TaskStatus.DONE
                ),
                make_task(
                    "004", "API", outputs=["d.rs"], depends_on=["003"], status=TaskStatus.DONE
                ),
            ]
        )
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = _two_spec_graph()

        # Re-plan AUTH with 3 tasks instead of 2
        new_auth_plan = _make_spec_plan(
            [
                {"title": "A1", "outputs": ["a.rs"]},
                {"title": "A2", "outputs": ["b.rs"], "depends_on": [1]},
                {"title": "A3", "outputs": ["c.rs"], "depends_on": [2]},
            ]
        )

        plan, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={"AUTH": new_auth_plan},
            changed_spec_ids={"AUTH"},
            graph=graph,
            parsed_smds=smds,
        )

        # 3 AUTH + 2 API = 5 tasks
        assert len(plan.tasks) == 5
        # First API task (004) should depend on last AUTH task (003)
        api_first = plan.tasks[3]
        assert api_first.spec == "API"
        assert "003" in api_first.depends_on

        # Second API task (005) should depend on first API task (004)
        api_second = plan.tasks[4]
        assert "004" in api_second.depends_on

    def test_changed_tasks_all_pending(self):
        """Tasks for the changed spec are always pending."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", status=TaskStatus.DONE),
                make_task("002", "API", depends_on=["001"], status=TaskStatus.DONE),
            ]
        )
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = _two_spec_graph()

        new_auth_plan = _make_spec_plan([{"title": "Auth new"}])

        plan, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={"AUTH": new_auth_plan},
            changed_spec_ids={"AUTH"},
            graph=graph,
            parsed_smds=smds,
        )

        assert plan.tasks[0].status == TaskStatus.PENDING
        assert plan.tasks[1].status == TaskStatus.DONE

    def test_cross_spec_interfaces_preserved(self):
        """Preserved tasks keep their cross_spec_interfaces."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", status=TaskStatus.DONE),
                PlanTask(
                    id="002",
                    spec="API",
                    title="API task",
                    description="",
                    outputs=[],
                    depends_on=["001"],
                    spec_refs=[],
                    arch_refs=[],
                    status=TaskStatus.DONE,
                    verify="",
                    cross_spec_interfaces=["AUTH"],
                ),
            ]
        )
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = _two_spec_graph()

        new_auth_plan = _make_spec_plan([{"title": "Auth new"}])

        plan, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={"AUTH": new_auth_plan},
            changed_spec_ids={"AUTH"},
            graph=graph,
            parsed_smds=smds,
        )

        api_task = next(t for t in plan.tasks if t.spec == "API")
        assert api_task.cross_spec_interfaces == ["AUTH"]

    def test_no_change_preserves_all(self):
        """If changed_spec_ids has a spec not in graph, nothing breaks."""
        existing = _make_existing_plan(
            [
                make_task("001", "AUTH", status=TaskStatus.DONE),
            ]
        )
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )

        # Changed spec doesn't exist in graph — should still produce valid plan
        plan, _ = incremental_merge_plan(
            existing_plan=existing,
            new_spec_plans={},
            changed_spec_ids={"NONEXISTENT"},
            graph=graph,
            parsed_smds=smds,
        )

        assert len(plan.tasks) == 1
        assert plan.tasks[0].status == TaskStatus.DONE


class TestRemapTaskDirectories:
    def test_renames_preserved_directories(self, temp_dir: Path):
        tasks_dir = temp_dir / "tasks"
        # Old dirs: 001-auth, 002-api
        (tasks_dir / "001-auth-scaffold").mkdir(parents=True)
        (tasks_dir / "001-auth-scaffold" / "prompt.md").write_text("auth prompt")
        (tasks_dir / "002-api-scaffold").mkdir(parents=True)
        (tasks_dir / "002-api-scaffold" / "prompt.md").write_text("api prompt")

        old_plan = _make_existing_plan(
            [
                make_task("001", "AUTH"),
                make_task("002", "API"),
            ]
        )
        # AUTH was re-planned (changed), API preserved and remapped 002 -> 003
        id_remap = {"002": "003"}
        remap_task_directories(tasks_dir, id_remap, {"AUTH"}, old_plan)

        dirs = sorted(d.name for d in tasks_dir.iterdir() if d.is_dir())
        # 001-auth-scaffold removed (changed spec), 002-api-scaffold -> 003-api-scaffold
        assert "001-auth-scaffold" not in dirs
        assert "002-api-scaffold" not in dirs
        assert "003-api-scaffold" in dirs
        assert (tasks_dir / "003-api-scaffold" / "prompt.md").read_text() == "api prompt"

    def test_removes_orphaned_changed_spec_dirs(self, temp_dir: Path):
        tasks_dir = temp_dir / "tasks"
        (tasks_dir / "001-auth-scaffold").mkdir(parents=True)
        (tasks_dir / "002-auth-types").mkdir(parents=True)
        (tasks_dir / "003-api-scaffold").mkdir(parents=True)

        old_plan = _make_existing_plan(
            [
                make_task("001", "AUTH"),
                make_task("002", "AUTH"),
                make_task("003", "API"),
            ]
        )

        id_remap = {"003": "001"}
        remap_task_directories(tasks_dir, id_remap, {"AUTH"}, old_plan)

        dirs = sorted(d.name for d in tasks_dir.iterdir() if d.is_dir())
        assert "001-api-scaffold" in dirs
        assert "001-auth-scaffold" not in dirs
        assert "002-auth-types" not in dirs

    def test_handles_nonexistent_tasks_dir(self, temp_dir: Path):
        tasks_dir = temp_dir / "tasks"
        old_plan = _make_existing_plan([make_task("001", "AUTH")])
        # Should not raise
        remap_task_directories(tasks_dir, {}, {"AUTH"}, old_plan)

    def test_two_phase_rename_avoids_collision(self, temp_dir: Path):
        """When 001 -> 002 and 002 -> 001, two-phase rename prevents data loss."""
        tasks_dir = temp_dir / "tasks"
        (tasks_dir / "001-task-a").mkdir(parents=True)
        (tasks_dir / "001-task-a" / "prompt.md").write_text("A")
        (tasks_dir / "002-task-b").mkdir(parents=True)
        (tasks_dir / "002-task-b" / "prompt.md").write_text("B")

        old_plan = _make_existing_plan([make_task("001", "X"), make_task("002", "X")])

        id_remap = {"001": "002", "002": "001"}
        remap_task_directories(tasks_dir, id_remap, set(), old_plan)

        assert (tasks_dir / "002-task-a" / "prompt.md").read_text() == "A"
        assert (tasks_dir / "001-task-b" / "prompt.md").read_text() == "B"


class TestRemapBuildState:
    def test_remaps_preserved_task_ids(self, temp_dir: Path):
        state_filepath = temp_dir / "state.toml"
        state = BuildState()
        state.set("001", TaskState("sha256:aaa", "sha256:bbb", ["a.rs"]))
        state.set("002", TaskState("sha256:ccc", "sha256:ddd", ["b.rs"]))
        state.set("003", TaskState("sha256:eee", "sha256:fff", ["c.rs"]))
        write_state(state, state_filepath)

        old_plan = _make_existing_plan(
            [
                make_task("001", "AUTH"),
                make_task("002", "DB"),
                make_task("003", "API"),
            ]
        )

        id_remap = {"002": "003", "003": "004"}
        remap_build_state(state_filepath, id_remap, {"AUTH"}, old_plan)

        loaded = load_state(state_filepath)
        # AUTH task 001 removed (changed spec)
        assert loaded.get("001") is None
        # DB 002 -> 003
        assert loaded.get("003") is not None
        assert loaded.get("003").input_hash == "sha256:ccc"
        # API 003 -> 004
        assert loaded.get("004") is not None
        assert loaded.get("004").input_hash == "sha256:eee"
        # Old keys gone
        assert loaded.get("002") is None

    def test_handles_nonexistent_state_file(self, temp_dir: Path):
        state_filepath = temp_dir / "nonexistent.toml"
        old_plan = _make_existing_plan([make_task("001", "AUTH")])
        # Should not raise
        remap_build_state(state_filepath, {}, {"AUTH"}, old_plan)

    def test_removes_changed_spec_entries(self, temp_dir: Path):
        state_filepath = temp_dir / "state.toml"
        state = BuildState()
        state.set("001", TaskState("h1", "h2", ["a.rs"]))
        state.set("002", TaskState("h3", "h4", ["b.rs"]))
        write_state(state, state_filepath)

        old_plan = _make_existing_plan([make_task("001", "AUTH"), make_task("002", "AUTH")])

        remap_build_state(state_filepath, {}, {"AUTH"}, old_plan)

        loaded = load_state(state_filepath)
        assert loaded.get("001") is None
        assert loaded.get("002") is None
