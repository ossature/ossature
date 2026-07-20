"""Generate per-spec task plans from the LLM, plus the spec snapshots and
diffs that drive incremental re-planning.
"""

import difflib
from pathlib import Path

import content_types
from pydantic_ai import Agent, ModelRetry

from ossature.audit.graph import SpecGraph
from ossature.audit.planner.merge import incremental_merge_plan, merge_into_global_plan
from ossature.audit.verify_validator import check_verify_commands, format_validator_errors
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.audit import SpecAuditReport
from ossature.models.plan import (
    Plan,
    PlannerTask,
    PlanTask,
    PreservedTaskRef,
    SpecTaskPlan,
)
from ossature.models.smd import SMDSpec
from ossature.promptspec import render, resolve_profile
from ossature.promptspec.profile import LanguageProfile
from ossature.renderer.amd import render_amd
from ossature.renderer.smd import render_smd
from ossature.shared.llm import UsageTracker, run_agent_sync
from ossature.verification.tasks import VerifyTaskSpec


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
        lines.append(f"- description: {task.description}")
        lines.append(f"- outputs: {task.outputs}")
        if task.depends_on:
            lines.append(f"- depends_on: {task.depends_on}")
        if task.spec_refs:
            lines.append(f"- spec_refs: {task.spec_refs}")
        if task.arch_refs:
            lines.append(f"- arch_refs: {task.arch_refs}")
        if not task.verify:
            lines.append("- verify: ")
        elif len(task.verify) == 1:
            lines.append(f"- verify: {task.verify[0]}")
        else:
            lines.append(f"- verify: {task.verify}")
        if task.context_files:
            lines.append(f"- context_files: {task.context_files}")
        if task.source:
            lines.append(f"- source: {task.source}")
        lines.append("")
    return "\n".join(lines)


def _resolve_preserved_refs(
    spec_plan: SpecTaskPlan,
    previous_tasks: list[PlanTask],
) -> SpecTaskPlan:
    """Resolve PreservedTaskRef entries into PlannerTask using previous task data.

    Invalid references (out-of-range previous_index) are replaced with a
    PlannerTask that has empty fields. The LLM's retry mechanism or the
    carry-over matching in incremental_merge_plan will handle recovery.
    """
    resolved: list[PlannerTask | PreservedTaskRef] = []
    for task in spec_plan.tasks:
        if not isinstance(task, PreservedTaskRef):
            resolved.append(task)
            continue

        idx = task.previous_index - 1
        if idx < 0 or idx >= len(previous_tasks):
            # Invalid ref — emit a minimal PlannerTask so planning continues.
            resolved.append(
                PlannerTask(
                    title=f"[unresolved ref: previous_index={task.previous_index}]",
                    description="",
                    outputs=[],
                    depends_on=task.depends_on,
                    spec_refs=[],
                    arch_refs=[],
                    verify=["true"],
                )
            )
            continue

        old = previous_tasks[idx]
        resolved.append(
            PlannerTask(
                title=old.title,
                description=old.description,
                outputs=list(old.outputs),
                depends_on=task.depends_on,
                spec_refs=list(old.spec_refs),
                arch_refs=list(old.arch_refs),
                verify=old.verify,
                context_files=list(old.context_files),
                source=list(old.source),
                covers=list(old.covers),
            )
        )

    return SpecTaskPlan(tasks=resolved)


def pick_planner_spec_id(spec_diff: str | None, previous_tasks: list[PlanTask] | None) -> str:
    """Pick the planner PromptSpec id based on whether this is a re-plan.

    plan.replan carries the preservation rules. plan.initial omits them
    so the model never sees instructions for a mode it isn't in. Re-plan
    mode requires both a spec diff and a previous task list, matching the
    same condition the user prompt assembly uses to include them.
    """
    if spec_diff and previous_tasks:
        return "audit.plan_replan"
    return "audit.plan_initial"


def format_vmd_target_line(vt: VerifyTaskSpec) -> str:
    """One planner-prompt line describing what a verify task checks.

    The planner never sees the VMD itself, so this line is its only dedup
    signal: the target function (or the scenario names) plus the
    requirements the cases cover.
    """
    line = f"- {vt.title.removeprefix('Verify: ')} ({vt.vmd_file})"
    if vt.case_labels:
        line += ": " + "; ".join(vt.case_labels)
    if vt.covers:
        line += f" [covers: {', '.join(vt.covers)}]"
    return line


def validate_verify_commands(plan: SpecTaskPlan, profile: LanguageProfile) -> SpecTaskPlan:
    """Planner output validator: reject a plan whose verify commands are wrong
    for the target language, so the model retries."""
    errors = check_verify_commands(plan, profile)
    if errors:
        raise ModelRetry(format_validator_errors(errors))
    return plan


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
    verify_tasks: list[VerifyTaskSpec] | None = None,
) -> SpecTaskPlan:
    model = config.llm.model_for("planner")
    spec_id = pick_planner_spec_id(spec_diff, previous_tasks)
    agent = Agent(
        model,
        output_type=SpecTaskPlan,
        system_prompt=render(spec_id, language=config.output.language),
        retries={"output": config.llm.retries},
    )

    profile = resolve_profile(config.output.language)

    @agent.output_validator
    def _validate_verify_commands(plan: SpecTaskPlan) -> SpecTaskPlan:
        # Fires only inside a live agent run; the logic is unit-tested via
        # validate_verify_commands directly.
        return validate_verify_commands(plan, profile)  # pragma: no cover

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
            "For tasks unaffected by the diff, emit a PreservedTaskRef with the task's "
            "1-based index. Only emit a full PlannerTask for new or modified tasks.\n"
        )
        sections.append(_format_previous_tasks(previous_tasks))

    if audit_report and audit_report.findings:
        sections.append(
            "\n## Audit Findings\n"
            "Account for the following findings when planning. Avoid generating "
            "tasks that would hit these known spec issues:\n"
        )
        for finding in audit_report.findings:
            sections.append(
                f"- [{finding.severity.value.upper()}] {finding.location}: {finding.issue}"
            )

    if config.build.setup:
        sections.append(
            f"\n## Build Setup Command\n"
            f"This setup command runs before the first task:\n"
            f"```\n{config.build.setup}\n```\n"
            f"Your first task should assume it has already run. Do not generate "
            f"scaffolding tasks that duplicate what it produces."
        )

    if verify_tasks:
        target_lines = "\n".join(format_vmd_target_line(vt) for vt in verify_tasks)
        sections.append(
            "\n## Author Verification Cases (VMD)\n"
            "The spec author wrote executable verification cases for the "
            "targets below. Ossature appends deterministic verification "
            "tasks for them after your tasks, so do not plan test tasks "
            "that would duplicate these cases. Plan tests only for behavior "
            "they do not cover, and set `covers` on those test tasks so "
            "requirement coverage counts them.\n\n" + target_lines
        )

    if context_inventory:
        file_lines = []
        for f in context_inventory:
            mime_type = content_types.get_content_type(f) or "application/octet-stream"
            file_lines.append(f"- `{f}` ({mime_type})")
        sections.append(
            "\n## Context Files\n\n"
            "The following files are available in the project's context "
            "directory. These are pre-existing assets (audio, images, "
            "reference code, documentation, and so on) that may be useful "
            "during implementation.\n\n" + "\n".join(file_lines) + "\n\n"
            "Two ways to use them in a task. For files the LLM should READ as "
            "reference (example code, docs, spec snippets), list them in the "
            "task's `context_files` field. Text files get included in the "
            "task's prompt; binary assets are exposed via tools so the "
            "implementer can copy them into the output directory (often "
            "under `assets/` or `sounds/`).\n\n"
            "For files that should be copied verbatim with no transformation "
            "(binary assets, fixtures, reference data), emit a copy task "
            'instead. Set `source = ["context://<path-or-glob>"]` and leave '
            "`verify` empty. The build system copies matched files directly "
            "without calling the LLM. Source and output patterns pair 1:1 by "
            "index, and any `*` or `**` wildcard in source must align with one "
            "in outputs so each matched basename is preserved. Typical "
            "candidates are opaque/binary assets like `.mp3`, `.wav`, `.png`, "
            "`.ttf`, `.pdf`, fonts, and fixtures where the output is "
            "byte-identical to the context file."
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


def generate_plan(
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    graph: SpecGraph,
    spec_reports: dict[str, SpecAuditReport],
    changed_spec_ids: set[str] | None = None,
    existing_plan: Plan | None = None,
    tracker: UsageTracker | None = None,
    verify_tasks_by_spec: dict[str, list[VerifyTaskSpec]] | None = None,
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
                    # Verify tasks are synthesized fresh from the VMDs, not
                    # replanned; the LLM never sees them.
                    previous_tasks = [
                        t for t in existing_plan.tasks if t.spec == spec_id and t.kind != "verify"
                    ]
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
                verify_tasks=(verify_tasks_by_spec or {}).get(spec_id),
            )

            if previous_tasks:
                spec_plan = _resolve_preserved_refs(spec_plan, previous_tasks)

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
            verify_tasks_by_spec=verify_tasks_by_spec,
        )
        return plan, id_remap, matched_old_ids

    return (
        merge_into_global_plan(spec_plans, graph, parsed_smds, verify_tasks_by_spec),
        None,
        None,
    )
