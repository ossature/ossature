"""Assemble per-spec plans into one globally-numbered plan.

Two entry points: merge_into_global_plan for a fresh plan, and
incremental_merge_plan which preserves unchanged specs' tasks and carries
over status by output match.
"""

from datetime import UTC, datetime

from ossature.audit.graph import SpecGraph
from ossature.models.plan import (
    Plan,
    PlanMeta,
    PlannerTask,
    PlanTask,
    SpecTaskPlan,
    TaskStatus,
)
from ossature.models.smd import SMDSpec
from ossature.verification.tasks import VerifyTaskSpec


def _match_old_task(
    outputs: list[str],
    old_tasks_by_outputs: dict[frozenset[str], list[PlanTask]],
) -> PlanTask | None:
    """Find a unique old task matching by exact outputs set.

    Returns the old task if exactly one match exists. Returns None for
    no matches or ambiguous matches (multiple old tasks with same outputs).
    """
    key = frozenset(outputs)
    candidates = old_tasks_by_outputs.get(key)
    if candidates and len(candidates) == 1:
        return candidates[0]
    return None


def _carry_over_status(old_status: TaskStatus) -> TaskStatus:
    """Determine status for a carried-over task. FAILED resets to PENDING."""
    if old_status in (TaskStatus.DONE, TaskStatus.MANUAL, TaskStatus.SKIPPED):
        return old_status
    return TaskStatus.PENDING


def _wire_fresh_task(
    planner_task: PlannerTask,
    local_idx: int,
    local_to_global: dict[int, str],
    smd_deps: dict[str, list[str]],
    spec_id: str,
    spec_last_task: dict[str, str],
    all_tasks: list[PlanTask],
) -> tuple[list[str], list[str], list[str]]:
    """Compute (depends_on, cross_spec_interfaces, inject_files) for a freshly
    planned task. The first task also depends on each upstream spec's last
    task; inject_files carries the outputs of same-spec dependencies."""
    depends_on = [local_to_global[d] for d in planner_task.depends_on if d in local_to_global]
    if local_idx == 1:
        for dep_spec_id in smd_deps.get(spec_id, []):
            dep_id = spec_last_task.get(dep_spec_id)
            if dep_id is not None and dep_id not in depends_on:
                depends_on.append(dep_id)

    cross_spec_interfaces = [
        dep_spec_id for dep_spec_id in smd_deps.get(spec_id, []) if dep_spec_id in spec_last_task
    ]

    inject_files: list[str] = []
    for dep_global_id in depends_on:
        for existing_task in all_tasks:
            if existing_task.id == dep_global_id and existing_task.spec == spec_id:
                inject_files.extend(existing_task.outputs)

    return depends_on, cross_spec_interfaces, inject_files


def _make_fresh_plan_task(
    planner_task: PlannerTask,
    global_id: str,
    spec_id: str,
    depends_on: list[str],
    cross_spec_interfaces: list[str],
    inject_files: list[str],
    status: TaskStatus = TaskStatus.PENDING,
    notes: str = "",
) -> PlanTask:
    """Build a PlanTask from a freshly planned PlannerTask and its wiring."""
    return PlanTask(
        id=global_id,
        spec=spec_id,
        title=planner_task.title,
        description=planner_task.description,
        outputs=planner_task.outputs,
        depends_on=depends_on,
        spec_refs=list(planner_task.spec_refs),
        arch_refs=list(planner_task.arch_refs),
        status=status,
        verify=planner_task.verify,
        inject_files=inject_files,
        cross_spec_interfaces=cross_spec_interfaces,
        context_files=list(planner_task.context_files),
        source=list(planner_task.source),
        covers=list(planner_task.covers),
        notes=notes,
    )


def _append_verify_tasks(
    spec_id: str,
    verify_specs: list[VerifyTaskSpec],
    all_tasks: list[PlanTask],
    spec_last_task: dict[str, str],
    counter: int,
    old_tasks_by_outputs: dict[frozenset[str], list[PlanTask]] | None = None,
    id_remap: dict[str, str] | None = None,
    matched_old_ids: set[str] | None = None,
) -> int:
    """Append deterministic verify tasks after a spec's implementation tasks.

    Each verify task depends on the final producer of its target file (or the
    spec's last implementation task when the target cannot be resolved), and
    the spec's last-task pointer moves to the last verify task, so cross-spec
    dependents build on verified code. Returns the updated global counter.
    """
    impl_last = spec_last_task.get(spec_id, "")
    for vt in verify_specs:
        counter += 1
        global_id = f"{counter:03d}"

        producer = ""
        if vt.target_file:
            for t in reversed(all_tasks):
                if t.spec == spec_id and t.kind != "verify" and vt.target_file in t.outputs:
                    producer = t.id
                    break
        depends_on = [producer or impl_last] if (producer or impl_last) else []

        status = TaskStatus.PENDING
        notes = ""
        if old_tasks_by_outputs is not None:
            old_match = _match_old_task(vt.outputs, old_tasks_by_outputs)
            if old_match is not None:
                status = _carry_over_status(old_match.status)
                notes = old_match.notes
                if matched_old_ids is not None:
                    matched_old_ids.add(old_match.id)
                if id_remap is not None:
                    id_remap[old_match.id] = global_id

        all_tasks.append(
            PlanTask(
                id=global_id,
                spec=spec_id,
                kind="verify",
                vmd_file=vt.vmd_file,
                vmd_group=vt.vmd_group,
                title=vt.title,
                description=vt.description,
                outputs=list(vt.outputs),
                depends_on=depends_on,
                spec_refs=[],
                arch_refs=[],
                status=status,
                verify=list(vt.verify),
                covers=list(vt.covers),
                notes=notes,
            )
        )
        spec_last_task[spec_id] = global_id
    return counter


def merge_into_global_plan(
    spec_plans: dict[str, SpecTaskPlan],
    graph: SpecGraph,
    parsed_smds: list[SMDSpec],
    verify_tasks_by_spec: dict[str, list[VerifyTaskSpec]] | None = None,
) -> Plan:
    all_tasks: list[PlanTask] = []
    global_counter = 0

    spec_local_to_global: dict[str, dict[int, str]] = {}
    spec_last_task: dict[str, str] = {}

    smd_deps: dict[str, list[str]] = {smd.spec_id: list(smd.depends) for smd in parsed_smds}

    for level in graph.levels:
        for spec_id in level:
            if spec_id not in spec_plans:
                continue

            spec_plan = spec_plans[spec_id]
            local_to_global: dict[int, str] = {}

            for local_idx, planner_task in enumerate(spec_plan.tasks, start=1):
                if not isinstance(planner_task, PlannerTask):
                    raise TypeError("merge_into_global_plan expects resolved PlannerTask entries")
                global_counter += 1
                global_id = f"{global_counter:03d}"
                local_to_global[local_idx] = global_id

                depends_on, cross_spec_interfaces, inject_files = _wire_fresh_task(
                    planner_task,
                    local_idx,
                    local_to_global,
                    smd_deps,
                    spec_id,
                    spec_last_task,
                    all_tasks,
                )
                all_tasks.append(
                    _make_fresh_plan_task(
                        planner_task,
                        global_id,
                        spec_id,
                        depends_on,
                        cross_spec_interfaces,
                        inject_files,
                    )
                )
            spec_local_to_global[spec_id] = local_to_global

            if spec_plan.tasks:
                spec_last_task[spec_id] = local_to_global[len(spec_plan.tasks)]

            if verify_tasks_by_spec and spec_id in verify_tasks_by_spec:
                global_counter = _append_verify_tasks(
                    spec_id,
                    verify_tasks_by_spec[spec_id],
                    all_tasks,
                    spec_last_task,
                    global_counter,
                )

    # Collect ordered spec IDs
    ordered_specs = [
        spec_id for level in graph.levels for spec_id in level if spec_id in spec_plans
    ]

    meta = PlanMeta(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        total_tasks=len(all_tasks),
        specs=ordered_specs,
    )

    return Plan(meta=meta, tasks=all_tasks)


def incremental_merge_plan(
    existing_plan: Plan,
    new_spec_plans: dict[str, SpecTaskPlan],
    changed_spec_ids: set[str],
    graph: SpecGraph,
    parsed_smds: list[SMDSpec],
    verify_tasks_by_spec: dict[str, list[VerifyTaskSpec]] | None = None,
) -> tuple[Plan, dict[str, str], set[str]]:
    smd_deps: dict[str, list[str]] = {smd.spec_id: list(smd.depends) for smd in parsed_smds}

    # Which spec owned each task ID in the old plan, for classifying
    # preserved-task dependencies as intra-spec vs cross-spec
    old_spec_of: dict[str, str] = {task.id: task.spec for task in existing_plan.tasks}

    # Collect preserved tasks grouped by spec
    preserved_by_spec: dict[str, list[PlanTask]] = {}
    for task in existing_plan.tasks:
        if task.spec not in changed_spec_ids:
            preserved_by_spec.setdefault(task.spec, []).append(task)

    # Index old changed-spec tasks by outputs for carry-over matching
    old_tasks_by_outputs: dict[str, dict[frozenset[str], list[PlanTask]]] = {}
    for task in existing_plan.tasks:
        if task.spec in changed_spec_ids:
            spec_index = old_tasks_by_outputs.setdefault(task.spec, {})
            key = frozenset(task.outputs)
            spec_index.setdefault(key, []).append(task)

    # Build the merged task list in topological order
    all_tasks: list[PlanTask] = []
    global_counter = 0
    id_remap: dict[str, str] = {}  # old_id -> new_id
    matched_old_ids: set[str] = set()  # old task IDs carried over from changed specs
    spec_last_task: dict[str, str] = {}

    for level in graph.levels:
        for spec_id in level:
            if spec_id in changed_spec_ids:
                # Use freshly planned tasks
                if spec_id not in new_spec_plans:
                    continue
                spec_plan = new_spec_plans[spec_id]
                local_to_global: dict[int, str] = {}
                spec_output_index = old_tasks_by_outputs.get(spec_id, {})

                for local_idx, planner_task in enumerate(spec_plan.tasks, start=1):
                    if not isinstance(planner_task, PlannerTask):
                        raise TypeError(
                            "incremental_merge_plan expects resolved PlannerTask entries"
                        )
                    global_counter += 1
                    global_id = f"{global_counter:03d}"
                    local_to_global[local_idx] = global_id

                    depends_on, cross_spec_interfaces, inject_files = _wire_fresh_task(
                        planner_task,
                        local_idx,
                        local_to_global,
                        smd_deps,
                        spec_id,
                        spec_last_task,
                        all_tasks,
                    )

                    # Try to match against old task by outputs for status carry-over
                    old_match = _match_old_task(planner_task.outputs, spec_output_index)
                    if old_match is not None:
                        status = _carry_over_status(old_match.status)
                        notes = old_match.notes
                        matched_old_ids.add(old_match.id)
                        id_remap[old_match.id] = global_id
                    else:
                        status = TaskStatus.PENDING
                        notes = ""

                    all_tasks.append(
                        _make_fresh_plan_task(
                            planner_task,
                            global_id,
                            spec_id,
                            depends_on,
                            cross_spec_interfaces,
                            inject_files,
                            status=status,
                            notes=notes,
                        )
                    )

                if spec_plan.tasks:
                    spec_last_task[spec_id] = local_to_global[len(spec_plan.tasks)]

                if verify_tasks_by_spec and spec_id in verify_tasks_by_spec:
                    global_counter = _append_verify_tasks(
                        spec_id,
                        verify_tasks_by_spec[spec_id],
                        all_tasks,
                        spec_last_task,
                        global_counter,
                        old_tasks_by_outputs=spec_output_index,
                        id_remap=id_remap,
                        matched_old_ids=matched_old_ids,
                    )
            else:
                # Preserve existing tasks, re-number and remap depends_on
                tasks = preserved_by_spec.get(spec_id, [])
                for task_idx, task in enumerate(tasks):
                    global_counter += 1
                    new_id = f"{global_counter:03d}"
                    id_remap[task.id] = new_id

                    # Intra-spec deps remap to their new IDs; cross-spec deps
                    # re-wire to the upstream spec's new last task
                    new_depends_on: list[str] = []
                    for d in task.depends_on:
                        dep_spec_id = old_spec_of.get(d, spec_id)
                        if dep_spec_id == spec_id:
                            remapped = id_remap.get(d, d)
                            if remapped not in new_depends_on:
                                new_depends_on.append(remapped)
                        else:
                            rewired = spec_last_task.get(dep_spec_id)
                            if rewired is not None and rewired not in new_depends_on:
                                new_depends_on.append(rewired)

                    # Ensure the first task of the spec depends on each upstream spec
                    if task_idx == 0:
                        for dep_spec_id in smd_deps.get(spec_id, []):
                            new_dep = spec_last_task.get(dep_spec_id)
                            if new_dep is not None and new_dep not in new_depends_on:
                                new_depends_on.append(new_dep)

                    new_inject = list(task.inject_files)

                    new_task = PlanTask(
                        id=new_id,
                        spec=task.spec,
                        title=task.title,
                        description=task.description,
                        outputs=task.outputs,
                        depends_on=new_depends_on,
                        spec_refs=task.spec_refs,
                        arch_refs=task.arch_refs,
                        status=task.status,
                        verify=task.verify,
                        inject_files=new_inject,
                        cross_spec_interfaces=task.cross_spec_interfaces,
                        context_files=list(task.context_files),
                        source=list(task.source),
                        covers=list(task.covers),
                        notes=task.notes,
                    )
                    all_tasks.append(new_task)

                if tasks:
                    spec_last_task[spec_id] = f"{global_counter:03d}"

    ordered_specs = [
        spec_id
        for level in graph.levels
        for spec_id in level
        if spec_id in changed_spec_ids or spec_id in preserved_by_spec
    ]

    meta = PlanMeta(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        total_tasks=len(all_tasks),
        specs=ordered_specs,
    )

    plan = Plan(meta=meta, tasks=all_tasks)
    return plan, id_remap, matched_old_ids
