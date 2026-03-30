import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli
import tomli_w

from ossature.config.loader import OssatureConfig
from ossature.models.plan import PlanTask


@dataclass
class TaskState:
    input_hash: str
    output_hash: str
    created_files: list[str]
    edited_files: list[str] = field(default_factory=list)


@dataclass
class BuildState:
    tasks: dict[str, TaskState] = field(default_factory=dict)

    def get(self, task_id: str) -> TaskState | None:
        return self.tasks.get(task_id)

    def set(self, task_id: str, state: TaskState) -> None:
        self.tasks[task_id] = state


def compute_input_hash(prompt: str, task: PlanTask, config: OssatureConfig) -> str:
    """Hash the assembled prompt + context_files content.

    The prompt already includes: project brief, spec brief, task definition,
    spec_refs content, arch_refs content, and cross_spec_interfaces content.
    inject_files are NOT hashed here — dependency rebuilds are detected by
    tracking rebuilt task IDs in the build loop, which avoids false invalidation
    when a later task edits an injected file.
    """
    hasher = hashlib.sha256()
    hasher.update(prompt.encode())
    for filepath in sorted(task.context_files):
        full_path = config.context_path / filepath
        if full_path.exists():
            hasher.update(f"context:{filepath}".encode())
            hasher.update(full_path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def compute_output_hash(created_files: list[str], config: OssatureConfig) -> str:
    """Hash files created (owned) by a task. Edits to files from other tasks are excluded."""
    hasher = hashlib.sha256()
    for filepath in sorted(created_files):
        full_path = config.output_path / filepath
        if full_path.exists():
            hasher.update(filepath.encode())
            hasher.update(full_path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def get_task_created_files(task: PlanTask, tasks_dir: Path) -> list[str]:
    """Get created files for a DONE task from output.toml, falling back to task.outputs."""
    from ossature.build.builder import make_task_slug

    slug = make_task_slug(task)
    output_file = tasks_dir / f"{task.id}-{slug}" / "output.toml"
    if output_file.exists():
        try:
            with open(output_file, "rb") as f:
                data = tomli.load(f)
            created = data.get("created_files")
            if isinstance(created, list):
                return created
            # Fall back to "files" for output.toml written before ownership tracking
            files = data.get("files")
            if isinstance(files, list):
                return files
        except tomli.TOMLDecodeError, OSError:
            pass
    return list(task.outputs)


# Bump when the hash computation changes to force re-backfill of stored hashes.
STATE_VERSION = 2


def load_state(filepath: Path) -> BuildState:
    if not filepath.exists():
        return BuildState()
    try:
        with open(filepath, "rb") as f:
            data = tomli.load(f)
    except tomli.TOMLDecodeError, OSError:
        return BuildState()

    version = data.get("meta", {}).get("version", 1)
    if version < STATE_VERSION:
        return BuildState()

    tasks: dict[str, TaskState] = {}
    for task_id, task_data in data.get("tasks", {}).items():
        if not isinstance(task_data, dict):
            continue
        tasks[task_id] = TaskState(
            input_hash=task_data.get("input_hash", ""),
            output_hash=task_data.get("output_hash", ""),
            created_files=task_data.get("created_files", []),
            edited_files=task_data.get("edited_files", []),
        )
    return BuildState(tasks=tasks)


def write_state(state: BuildState, filepath: Path) -> None:
    data: dict[str, Any] = {"meta": {"version": STATE_VERSION}, "tasks": {}}
    for task_id in sorted(state.tasks):
        ts = state.tasks[task_id]
        entry: dict[str, Any] = {
            "input_hash": ts.input_hash,
            "output_hash": ts.output_hash,
            "created_files": ts.created_files,
        }
        if ts.edited_files:
            entry["edited_files"] = ts.edited_files
        data["tasks"][task_id] = entry

    filepath.parent.mkdir(parents=True, exist_ok=True)
    content = tomli_w.dumps(data)
    with open(filepath, "w") as f:
        f.write("# .ossature/state.toml — Build state (auto-generated, do not edit)\n\n")
        f.write(content)
