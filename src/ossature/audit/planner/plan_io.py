"""Read and write plan.toml, and reconcile task directories and build
state when an incremental re-plan renumbers tasks."""

import shutil
import warnings
from pathlib import Path
from typing import Any

import tomli
import tomli_w

from ossature.build.state import load_state, write_state
from ossature.models.plan import Plan, PlanMeta, PlanTask, TaskStatus


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
    set of files the agent may have written - those could include files
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
        if task.kind != "task":
            task_dict["kind"] = task.kind
            task_dict["vmd_file"] = task.vmd_file
            task_dict["vmd_group"] = task.vmd_group
        if task.inject_files:
            task_dict["inject_files"] = task.inject_files
        if task.cross_spec_interfaces:
            task_dict["cross_spec_interfaces"] = task.cross_spec_interfaces
        if task.context_files:
            task_dict["context_files"] = task.context_files
        if task.source:
            task_dict["source"] = [f"context://{s}" for s in task.source]
        if task.covers:
            task_dict["covers"] = task.covers
        if task.notes:
            task_dict["notes"] = task.notes
        data["task"].append(task_dict)

    content = tomli_w.dumps(data)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        f.write("# .ossature/plan.toml - Generated by `ossature audit`, editable by architect\n")
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
            kind=t.get("kind", "task"),
            vmd_file=t.get("vmd_file", ""),
            vmd_group=t.get("vmd_group", ""),
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
            source=t.get("source", []),
            covers=t.get("covers", []),
            notes=t.get("notes", ""),
        )
        for t in data.get("task", [])
    ]

    for task in tasks:
        if task.source and task.verify:
            warnings.warn(
                f"plan.toml task {task.id}: `verify` is ignored for copy tasks "
                f"(source is set). Set verify = [] to silence this warning.",
                UserWarning,
                stacklevel=2,
            )

    return Plan(meta=meta, tasks=tasks)
