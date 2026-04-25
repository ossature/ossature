import difflib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import content_types
import tomli
import tomli_w
from pydantic_ai import Agent

from ossature.audit.graph import SpecGraph
from ossature.audit.prompts import PLAN_GENERATION_SYSTEM_PROMPT
from ossature.build.state import load_state, write_state
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.audit import SpecAuditReport
from ossature.models.plan import Plan, PlanMeta, PlanTask, SpecTaskPlan, TaskStatus
from ossature.models.smd import SMDSpec
from ossature.renderer.amd import render_amd
from ossature.renderer.smd import render_smd
from ossature.shared.llm import UsageTracker, run_agent_sync


def render_spec_snapshot(smd: SMDSpec, amds: list[AMDSpec] | None) -> str:
    """Render the spec content (SMD + AMDs) used as the planner's input.

    This is saved as a snapshot so that future incremental re-plans can diff
    the old spec content against the new to detect what changed.
    """
    sections: list[str] = []
    sections.append(render_smd(smd))

    if amds:
        sections.append("\n## Architecture Documents (AMD)\n")
        for amd in amds:
            sections.append(render_amd(amd))

    return "\n".join(sections)


def write_planner_snapshot(snapshot: str, spec_id: str, snapshots_dir: Path) -> None:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    filepath = snapshots_dir / f"{spec_id}.md"
    with open(filepath, "w") as f:
        f.write(snapshot)


def load_planner_snapshot(spec_id: str, snapshots_dir: Path) -> str | None:
    filepath = snapshots_dir / f"{spec_id}.md"
    if not filepath.exists():
        return None
    return filepath.read_text()


def compute_spec_diff(old_snapshot: str, new_snapshot: str) -> str | None:
    """Compute a unified diff between old and new spec snapshots.

    Returns the diff as a string, or None if the content is identical.
    """
    old_lines = old_snapshot.splitlines(keepends=True)
    new_lines = new_snapshot.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after"))
    if not diff_lines:
        return None
    return "".join(diff_lines)


def _format_previous_tasks(tasks: list[PlanTask]) -> str:
    """Format previous tasks compactly for the planner prompt."""
    lines: list[str] = []
    for i, task in enumerate(tasks, start=1):
        lines.append(f"### Task {i}")
        lines.append(f"- title: {task.title}")
        lines.append(f"- outputs: {task.outputs}")
        if task.depends_on:
            lines.append(f"- depends_on: {task.depends_on}")
        lines.append(f"- verify: {task.verify}")
        lines.append("")
    return "\n".join(lines)


def generate_spec_plan(
    config: OssatureConfig,
    smd: SMDSpec,
    amds: list[AMDSpec] | None,
    audit_report: SpecAuditReport | None,
    context_inventory: list[str] | None = None,
    spec_diff: str | None = None,
    previous_tasks: list[PlanTask] | None = None,
    tracker: UsageTracker | None = None,
    transcript_dir: Path | None = None,
) -> SpecTaskPlan:
    model = config.llm.model_for("planner")
    agent = Agent(
        model,
        output_type=SpecTaskPlan,
        system_prompt=PLAN_GENERATION_SYSTEM_PROMPT.format(language=config.output.language),
        retries=config.llm.retries,
    )

    sections: list[str] = []

    project_header = f"# Project: {config.name} v{config.version} ({config.output.language})"
    if config.output.framework:
        project_header += f" — Framework: {config.output.framework}"
    sections.append(project_header + "\n")

    sections.append("## Specification (SMD)\n")
    sections.append(render_spec_snapshot(smd, amds))

    if spec_diff and previous_tasks:
        sections.append("\n## Spec Changes (diff from previous version)\n")
        sections.append(f"```diff\n{spec_diff}```\n")
        sections.append("## Previous Task Plan\n")
        sections.append(
            "The following tasks were generated from the previous version of this spec. "
            "Preserve tasks unaffected by the diff — keep their title, outputs, and verify "
            "command identical. Only modify, add, or remove tasks impacted by the changes.\n"
        )
        sections.append(_format_previous_tasks(previous_tasks))

    if audit_report and audit_report.findings:
        sections.append("\n## Audit Findings (avoid these issues in planning)\n")
        for finding in audit_report.findings:
            sections.append(
                f"- [{finding.severity.value.upper()}] {finding.location}: {finding.issue}"
            )

    if config.build.setup:
        sections.append(
            f"\n## Build Setup Command\n"
            f"The following setup command runs before the first task:\n"
            f"```\n{config.build.setup}\n```\n"
            f"Do not generate tasks that duplicate what this command does."
        )

    if context_inventory:
        file_lines = []
        for f in context_inventory:
            mime_type = content_types.get_content_type(f)
            file_lines.append(f"- `{f}` ({mime_type})")
        sections.append(
            "\n## Context Files\n\n"
            "The following files are available in the project's context directory. "
            "These are pre-existing assets (audio, images, reference code, documentation, etc.) "
            "that may be useful during implementation.\n\n" + "\n".join(file_lines) + "\n\n"
            "For each task, list any context files it needs in the `context_files` field. "
            "The build system will include text files in the prompt and provide tools "
            "for the implementer to copy binary assets to the appropriate location "
            "within the output directory (e.g. an `assets/` or `sounds/` subdirectory, "
            "wherever fits the project structure)."
        )

    user_prompt = "\n".join(sections)

    result = run_agent_sync(
        agent,
        user_prompt,
        operation="plan generation",
        model_name=model,
        spec_id=smd.spec_id,
        tracker=tracker,
    )

    if transcript_dir is not None:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / "prompt.md").write_text(user_prompt)
        (transcript_dir / "response.json").write_text(result.output.model_dump_json(indent=2))

    return result.output


def merge_into_global_plan(
    spec_plans: dict[str, SpecTaskPlan],
    graph: SpecGraph,
    parsed_smds: list[SMDSpec],
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
                global_counter += 1
                global_id = f"{global_counter:03d}"
                local_to_global[local_idx] = global_id

                depends_on: list[str] = []
                for local_dep in planner_task.depends_on:
                    if local_dep in local_to_global:
                        depends_on.append(local_to_global[local_dep])

                if local_idx == 1 and smd_deps.get(spec_id):
                    for dep_spec_id in smd_deps[spec_id]:
                        if dep_spec_id in spec_last_task:
                            dep_id = spec_last_task[dep_spec_id]
                            if dep_id not in depends_on:
                                depends_on.append(dep_id)

                cross_spec_interfaces: list[str] = []
                if smd_deps.get(spec_id):
                    cross_spec_interfaces = [
                        dep_id for dep_id in smd_deps[spec_id] if dep_id in spec_last_task
                    ]

                inject_files: list[str] = []
                for dep_global_id in depends_on:
                    for existing_task in all_tasks:
                        if existing_task.id == dep_global_id and existing_task.spec == spec_id:
                            inject_files.extend(existing_task.outputs)

                spec_refs = [f"{spec_id}:{ref}" for ref in planner_task.spec_refs]
                arch_refs = [f"{spec_id}:{ref}" for ref in planner_task.arch_refs]

                task = PlanTask(
                    id=global_id,
                    spec=spec_id,
                    title=planner_task.title,
                    description=planner_task.description,
                    outputs=planner_task.outputs,
                    depends_on=depends_on,
                    spec_refs=spec_refs,
                    arch_refs=arch_refs,
                    status=TaskStatus.PENDING,
                    verify=planner_task.verify,
                    inject_files=inject_files,
                    cross_spec_interfaces=cross_spec_interfaces,
                    context_files=list(planner_task.context_files),
                )
                all_tasks.append(task)

            spec_local_to_global[spec_id] = local_to_global

            if spec_plan.tasks:
                spec_last_task[spec_id] = local_to_global[len(spec_plan.tasks)]

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


def generate_plan(
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    graph: SpecGraph,
    spec_reports: dict[str, SpecAuditReport],
    changed_spec_ids: set[str] | None = None,
    existing_plan: Plan | None = None,
    tracker: UsageTracker | None = None,
) -> tuple[Plan, dict[str, str] | None, set[str] | None]:
    spec_plans: dict[str, SpecTaskPlan] = {}

    context_inventory: list[str] = []
    if config.context_path.is_dir():
        for p in sorted(config.context_path.rglob("*")):
            if p.is_file():
                context_inventory.append(str(p.relative_to(config.context_path)))

    # Determine which specs need re-planning
    specs_to_replan = changed_spec_ids or {s.spec_id for s in parsed_smds}

    for level in graph.levels:
        for spec_id in level:
            if spec_id not in specs_to_replan:
                continue

            smd = next((s for s in parsed_smds if s.spec_id == spec_id), None)
            if smd is None:
                continue

            amds = amd_by_spec.get(spec_id)
            audit_report = spec_reports.get(spec_id)

            # Compute diff against previous snapshot for incremental re-plans
            new_snapshot = render_spec_snapshot(smd, amds)
            spec_diff: str | None = None
            previous_tasks: list[PlanTask] | None = None
            if changed_spec_ids is not None:
                old_snapshot = load_planner_snapshot(spec_id, config.metadata_snapshots_path)
                if old_snapshot is not None:
                    spec_diff = compute_spec_diff(old_snapshot, new_snapshot)
                if existing_plan is not None:
                    previous_tasks = [t for t in existing_plan.tasks if t.spec == spec_id]
                    if not previous_tasks:
                        previous_tasks = None

            spec_plan = generate_spec_plan(
                config,
                smd,
                amds,
                audit_report,
                context_inventory=context_inventory or None,
                spec_diff=spec_diff,
                previous_tasks=previous_tasks,
                tracker=tracker,
                transcript_dir=config.metadata_planners_path / spec_id,
            )
            spec_plans[spec_id] = spec_plan

            # Save snapshot of the spec content for future incremental re-plans
            write_planner_snapshot(new_snapshot, spec_id, config.metadata_snapshots_path)

    if existing_plan and changed_spec_ids:
        plan, id_remap, matched_old_ids = incremental_merge_plan(
            existing_plan=existing_plan,
            new_spec_plans=spec_plans,
            changed_spec_ids=changed_spec_ids,
            graph=graph,
            parsed_smds=parsed_smds,
        )
        return plan, id_remap, matched_old_ids

    return merge_into_global_plan(spec_plans, graph, parsed_smds), None, None


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


def incremental_merge_plan(
    existing_plan: Plan,
    new_spec_plans: dict[str, SpecTaskPlan],
    changed_spec_ids: set[str],
    graph: SpecGraph,
    parsed_smds: list[SMDSpec],
) -> tuple[Plan, dict[str, str], set[str]]:
    smd_deps: dict[str, list[str]] = {smd.spec_id: list(smd.depends) for smd in parsed_smds}

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
                    global_counter += 1
                    global_id = f"{global_counter:03d}"
                    local_to_global[local_idx] = global_id

                    depends_on: list[str] = []
                    for local_dep in planner_task.depends_on:
                        if local_dep in local_to_global:
                            depends_on.append(local_to_global[local_dep])

                    if local_idx == 1 and smd_deps.get(spec_id):
                        for dep_spec_id in smd_deps[spec_id]:
                            if dep_spec_id in spec_last_task:
                                dep_id = spec_last_task[dep_spec_id]
                                if dep_id not in depends_on:
                                    depends_on.append(dep_id)

                    cross_spec_interfaces: list[str] = []
                    if smd_deps.get(spec_id):
                        cross_spec_interfaces = [
                            dep_id for dep_id in smd_deps[spec_id] if dep_id in spec_last_task
                        ]

                    inject_files: list[str] = []
                    for dep_global_id in depends_on:
                        for existing_task in all_tasks:
                            if existing_task.id == dep_global_id and existing_task.spec == spec_id:
                                inject_files.extend(existing_task.outputs)

                    spec_refs = [f"{spec_id}:{ref}" for ref in planner_task.spec_refs]
                    arch_refs = [f"{spec_id}:{ref}" for ref in planner_task.arch_refs]

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

                    task = PlanTask(
                        id=global_id,
                        spec=spec_id,
                        title=planner_task.title,
                        description=planner_task.description,
                        outputs=planner_task.outputs,
                        depends_on=depends_on,
                        spec_refs=spec_refs,
                        arch_refs=arch_refs,
                        status=status,
                        verify=planner_task.verify,
                        inject_files=inject_files,
                        cross_spec_interfaces=cross_spec_interfaces,
                        context_files=list(planner_task.context_files),
                        notes=notes,
                    )
                    all_tasks.append(task)

                if spec_plan.tasks:
                    spec_last_task[spec_id] = local_to_global[len(spec_plan.tasks)]
            else:
                # Preserve existing tasks, re-number and remap depends_on
                tasks = preserved_by_spec.get(spec_id, [])
                for task in tasks:
                    global_counter += 1
                    new_id = f"{global_counter:03d}"
                    old_id = task.id
                    id_remap[old_id] = new_id

                    new_depends_on = [id_remap.get(d, d) for d in task.depends_on]

                    # Re-wire cross-spec dependencies to point to new last-task IDs
                    if smd_deps.get(spec_id):
                        for dep_spec_id in smd_deps[spec_id]:
                            if dep_spec_id in spec_last_task:
                                new_dep = spec_last_task[dep_spec_id]
                                # Replace any old cross-spec dep from this dep_spec
                                new_depends_on = [
                                    d
                                    for d in new_depends_on
                                    if d not in id_remap or id_remap.get(d, d) != d or d == new_dep
                                ]
                                # Ensure the first task of the spec depends on upstream spec
                                if task == tasks[0] and new_dep not in new_depends_on:
                                    new_depends_on.append(new_dep)

                    new_inject = [id_remap.get(f, f) for f in task.inject_files]

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


def remap_task_directories(
    tasks_dir: Path,
    id_remap: dict[str, str],
    changed_spec_ids: set[str],
    old_plan: Plan,
    matched_old_ids: set[str] | None = None,
) -> None:
    if not tasks_dir.exists():
        return

    matched = matched_old_ids or set()

    # Remove orphaned directories from changed specs (skip matched/carried-over tasks)
    old_changed_ids = {t.id for t in old_plan.tasks if t.spec in changed_spec_ids}
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        dir_id = task_dir.name.split("-", 1)[0]
        if dir_id in old_changed_ids and dir_id not in matched:
            shutil.rmtree(task_dir)

    # Rename preserved/matched task directories: use a temp name first to avoid collisions
    rename_pairs: list[tuple[Path, Path]] = []
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        dir_id = task_dir.name.split("-", 1)[0]
        if dir_id in id_remap:
            new_id = id_remap[dir_id]
            slug = task_dir.name.split("-", 1)[1] if "-" in task_dir.name else ""
            new_name = f"{new_id}-{slug}" if slug else new_id
            rename_pairs.append((task_dir, tasks_dir / new_name))

    # Two-phase rename to avoid collisions
    temp_pairs: list[tuple[Path, Path]] = []
    for src, dst in rename_pairs:
        if src == dst:
            continue
        temp = src.with_name(f"_tmp_{src.name}")
        src.rename(temp)
        temp_pairs.append((temp, dst))
    for temp, dst in temp_pairs:
        temp.rename(dst)


def remap_build_state(
    state_filepath: Path,
    id_remap: dict[str, str],
    changed_spec_ids: set[str],
    old_plan: Plan,
    matched_old_ids: set[str] | None = None,
) -> None:
    if not state_filepath.exists():
        return

    matched = matched_old_ids or set()

    state = load_state(state_filepath)
    old_changed_ids = {t.id for t in old_plan.tasks if t.spec in changed_spec_ids}

    new_tasks = {}
    for task_id, task_state in state.tasks.items():
        if task_id in old_changed_ids and task_id not in matched:
            continue
        new_id = id_remap.get(task_id, task_id)
        new_tasks[new_id] = task_state

    state.tasks = new_tasks
    write_state(state, state_filepath)


def collect_orphaned_output_files(
    old_plan: Plan,
    new_plan: Plan,
    changed_spec_ids: set[str],
) -> list[str]:
    """Return planned output file paths from old changed-spec tasks
    that are not claimed by any task in the new plan.

    Only considers task.outputs (explicitly planned files), not the full
    set of files the agent may have written — those could include files
    created by build.setup or other external processes.
    """
    old_files: set[str] = set()
    for task in old_plan.tasks:
        if task.spec in changed_spec_ids and task.status == TaskStatus.DONE:
            old_files.update(task.outputs)

    new_files: set[str] = set()
    for task in new_plan.tasks:
        new_files.update(task.outputs)

    return sorted(old_files - new_files)


def remove_orphaned_output_files(
    orphaned_files: list[str],
    output_dir: Path,
) -> list[str]:
    """Delete orphaned files from the output directory. Returns files actually removed."""
    removed: list[str] = []
    for filepath in orphaned_files:
        full_path = output_dir / filepath
        if full_path.exists():
            full_path.unlink()
            removed.append(filepath)
            # Remove empty parent directories up to output_dir
            parent = full_path.parent
            while parent != output_dir:
                try:
                    parent.rmdir()  # only removes if empty
                except OSError:
                    break
                parent = parent.parent
    return removed


def write_plan(plan: Plan, filepath: Path) -> None:
    data: dict[str, Any] = {
        "meta": {
            "generated_at": plan.meta.generated_at,
            "total_tasks": plan.meta.total_tasks,
            "specs": plan.meta.specs,
        },
        "task": [],
    }

    for task in plan.tasks:
        task_dict: dict[str, Any] = {
            "id": task.id,
            "spec": task.spec,
            "title": task.title,
            "description": task.description,
            "outputs": task.outputs,
            "depends_on": task.depends_on,
            "spec_refs": task.spec_refs,
            "arch_refs": task.arch_refs,
            "status": task.status.value,
            "verify": task.verify,
        }
        if task.inject_files:
            task_dict["inject_files"] = task.inject_files
        if task.cross_spec_interfaces:
            task_dict["cross_spec_interfaces"] = task.cross_spec_interfaces
        if task.context_files:
            task_dict["context_files"] = task.context_files
        if task.notes:
            task_dict["notes"] = task.notes
        data["task"].append(task_dict)

    content = tomli_w.dumps(data)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        f.write("# .ossature/plan.toml — Generated by `ossature audit`, editable by architect\n")
        f.write("# Re-run `ossature audit --replan` to regenerate (discards manual edits)\n\n")
        f.write(content)


def load_plan(filepath: Path) -> Plan | None:
    if not filepath.exists():
        return None

    try:
        with open(filepath, "rb") as f:
            data = tomli.load(f)
    except tomli.TOMLDecodeError:
        return None

    meta = PlanMeta(**data["meta"])
    tasks = [
        PlanTask(
            id=t["id"],
            spec=t["spec"],
            title=t["title"],
            description=t["description"],
            outputs=t["outputs"],
            depends_on=t["depends_on"],
            spec_refs=t["spec_refs"],
            arch_refs=t["arch_refs"],
            status=TaskStatus(t["status"]),
            verify=t["verify"],
            inject_files=t.get("inject_files", []),
            cross_spec_interfaces=t.get("cross_spec_interfaces", []),
            context_files=t.get("context_files", []),
            notes=t.get("notes", ""),
        )
        for t in data.get("task", [])
    ]

    return Plan(meta=meta, tasks=tasks)


def write_task_definitions(plan: Plan, tasks_dir: Path) -> None:
    for task in plan.tasks:
        slug = task.title.lower().replace(" ", "-").replace(":", "").replace("/", "-")
        task_dir = tasks_dir / f"{task.id}-{slug}"
        task_dir.mkdir(parents=True, exist_ok=True)

        task_data = {
            "id": task.id,
            "spec": task.spec,
            "title": task.title,
            "description": task.description,
            "outputs": task.outputs,
            "depends_on": task.depends_on,
            "spec_refs": task.spec_refs,
            "arch_refs": task.arch_refs,
            "status": task.status.value,
            "verify": task.verify,
        }
        if task.inject_files:
            task_data["inject_files"] = task.inject_files
        if task.cross_spec_interfaces:
            task_data["cross_spec_interfaces"] = task.cross_spec_interfaces
        if task.context_files:
            task_data["context_files"] = task.context_files
        if task.notes:
            task_data["notes"] = task.notes

        task_filepath = task_dir / "task.toml"
        with open(task_filepath, "wb") as f:
            tomli_w.dump(task_data, f)
