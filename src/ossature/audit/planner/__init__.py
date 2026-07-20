"""Planner: generate plan.toml from audited specs.

Split across planning (LLM plan generation), merge (assembling per-spec
plans into one), and plan_io (reading/writing plan.toml and reconciling
task state on re-plan). This module re-exports the public API.
"""

from ossature.audit.planner.merge import (
    incremental_merge_plan,
    merge_into_global_plan,
)
from ossature.audit.planner.plan_io import (
    collect_orphaned_output_files,
    load_plan,
    remap_build_state,
    remap_task_directories,
    remove_orphaned_output_files,
    write_plan,
)
from ossature.audit.planner.planning import (
    compute_spec_diff,
    format_vmd_target_line,
    generate_plan,
    generate_spec_plan,
    load_planner_snapshot,
    pick_planner_spec_id,
    render_spec_snapshot,
    write_planner_snapshot,
)

__all__ = [
    "collect_orphaned_output_files",
    "compute_spec_diff",
    "format_vmd_target_line",
    "generate_plan",
    "generate_spec_plan",
    "incremental_merge_plan",
    "load_plan",
    "load_planner_snapshot",
    "merge_into_global_plan",
    "pick_planner_spec_id",
    "remap_build_state",
    "remap_task_directories",
    "remove_orphaned_output_files",
    "render_spec_snapshot",
    "write_plan",
    "write_planner_snapshot",
]
