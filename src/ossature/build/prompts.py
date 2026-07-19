"""Prompt assembly for the implementer, fixer, and reviewer agents."""

import posixpath

import content_types

from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec, Component
from ossature.models.plan import Plan, PlanTask
from ossature.models.review import ReviewReport
from ossature.models.smd import SMDSpec
from ossature.renderer.amd import render_component, render_data_model, render_dependency
from ossature.renderer.smd import render_example, render_requirement


def _conformance_context(
    task: PlanTask,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    contract_paths: list[str],
) -> list[str]:
    """The spec sections a task must satisfy plus the contracts of the
    components it owns. Shared by the reviewer prompt and the review-fix prompt
    so both judge and fix against the same requirements, not a paraphrase.

    contract_paths is the subset of the task's outputs whose component contracts
    actually apply, the files this task is the final producer of. A file a later
    task rewrites is left out, so a scaffold is not judged against the finished
    component's contracts.
    """
    parts: list[str] = []

    smd = smd_map.get(task.spec)
    if smd and task.spec_refs:
        spec_parts: list[str] = []
        for ref in task.spec_refs:
            rendered = _render_spec_ref(smd, ref.strip())
            if rendered:
                spec_parts.append(rendered)
        if spec_parts:
            parts.append("<specification>\n" + "\n\n".join(spec_parts) + "\n</specification>")

    amds = amd_by_spec.get(task.spec)
    if amds:
        comp_parts = [render_component(c) for c in components_for_paths(amds, contract_paths)]
        if comp_parts:
            parts.append("<architecture>\n" + "\n\n".join(comp_parts) + "\n</architecture>")

    return parts


def assemble_review_prompt(
    task: PlanTask,
    config: OssatureConfig,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    contract_paths: list[str] | None = None,
) -> str:
    """Build the reviewer prompt: the task's intent, the spec sections it must
    satisfy, the contracts of the components it owns, and the generated source.

    Scoped to one task, the same material the implementer saw plus the code it
    produced, so the reviewer judges conformance without the whole project.
    """
    paths = task.outputs if contract_paths is None else contract_paths
    sections: list[str] = [f"<task>\n{task.title}\n\n{task.description}\n</task>"]
    sections.extend(_conformance_context(task, smd_map, amd_by_spec, paths))

    src_parts: list[str] = []
    for out in task.outputs:
        full_path = config.output_path / out
        if full_path.exists():
            src_parts.append(f"### {out}\n\n```\n{full_path.read_text()}\n```")
    if src_parts:
        sections.append("<generated_code>\n" + "\n\n".join(src_parts) + "\n</generated_code>")

    return "\n\n".join(sections)


def _task_is_reviewable(
    task: PlanTask, amds: list[AMDSpec] | None, contract_paths: list[str] | None = None
) -> bool:
    """Reviewable when the task generated code (not a verbatim copy) and there
    is something concrete to check it against: spec requirements or the declared
    contracts of a component it is the final producer of."""
    if task.source or not task.outputs:
        return False
    paths = task.outputs if contract_paths is None else contract_paths
    has_contracts = bool(amds and any(c.contracts for c in components_for_paths(amds, paths)))
    return bool(task.spec_refs) or has_contracts


def _render_spec_ref(smd: SMDSpec, section: str) -> str | None:
    s = section.lower()

    if s == "overview":
        return f"### Overview\n\n{smd.overview}"

    if s == "goals" and smd.goals:
        return "### Goals\n\n" + "\n".join(f"- {g}" for g in smd.goals)

    if s == "non-goals" and smd.non_goals:
        return "### Non-Goals\n\n" + "\n".join(f"- {g}" for g in smd.non_goals)

    if s == "constraints" and smd.constraints:
        return "### Constraints\n\n" + "\n".join(f"- {c}" for c in smd.constraints)

    if s == "acceptance criteria" and smd.acceptance_criteria:
        return "### Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in smd.acceptance_criteria)

    if s == "notes" and smd.notes:
        return f"### Notes\n\n{smd.notes}"

    if s == "requirements" and smd.requirements:
        rendered = "\n\n".join(render_requirement(r) for r in smd.requirements)
        return f"## Requirements\n\n{rendered}"

    if s == "examples" and smd.examples:
        rendered = "\n\n".join(render_example(e) for e in smd.examples)
        return f"## Examples\n\n{rendered}"

    # Match individual requirement by title
    for req in smd.requirements:
        if req.title.lower() == s:
            return render_requirement(req)

    # Match individual example by name
    for ex in smd.examples:
        if ex.name.lower() == s:
            return render_example(ex)

    return None


def _norm_rel_path(path: str) -> str:
    return posixpath.normpath(path.strip().replace("\\", "/")).lower()


def components_for_paths(amds: list[AMDSpec], paths: list[str]) -> list[Component]:
    """Components whose @path matches one of the given output-relative paths.

    This is the deterministic task-to-component link: both @path and task
    output paths are relative to the output directory, so ownership is a
    path comparison with no LLM in the loop. Paths are normalized so a
    hand-written './src/foo.py' still matches 'src/foo.py'.
    """
    wanted = {_norm_rel_path(p) for p in paths}
    return [comp for amd in amds for comp in amd.components if _norm_rel_path(comp.path) in wanted]


def final_output_paths(task: PlanTask, plan: Plan) -> list[str]:
    """Outputs of `task` that no later task in the plan also produces.

    A task that only scaffolds a file a later task rewrites is not the final
    producer of it, so that file's component contracts belong to the later task,
    not this one. Used to scope which contracts the reviewer holds a task to.
    """
    later: set[str] = set()
    seen_self = False
    for other in plan.tasks:
        if other.id == task.id:
            seen_self = True
            continue
        if seen_self:
            later.update(_norm_rel_path(o) for o in other.outputs)
    return [o for o in task.outputs if _norm_rel_path(o) not in later]


def _render_arch_ref(amds: list[AMDSpec], section: str) -> str | None:
    s = section.lower()

    if s == "overview":
        parts = [a.overview for a in amds if a.overview]
        return ("### Overview\n\n" + "\n\n".join(parts)) if parts else None

    if s == "dependencies":
        deps = [d for a in amds for d in a.dependencies]
        if not deps:
            return None
        return "### Dependencies\n\n" + "\n".join(render_dependency(d) for d in deps)

    if s == "flow":
        parts = [a.flow for a in amds if a.flow]
        if not parts:
            return None
        return "### Flow\n\n```\n" + "\n\n".join(parts) + "\n```"

    if s == "notes":
        parts = [a.notes for a in amds if a.notes]
        return ("### Notes\n\n" + "\n\n".join(parts)) if parts else None

    # Bare section refs (the planner examples use "data models" without a
    # name) render the whole section instead of being silently dropped.
    if s == "components":
        comps = [c for a in amds for c in a.components]
        if not comps:
            return None
        return "\n\n".join(render_component(c) for c in comps)

    if s == "data models":
        dms = [d for a in amds for d in a.data_models]
        if not dms:
            return None
        return "\n\n".join(render_data_model(d) for d in dms)

    if s.startswith("components >"):
        name = section.split(">", 1)[1].strip()
        for amd in amds:
            for comp in amd.components:
                if comp.name.lower() == name.lower():
                    return render_component(comp)
        return None

    if s.startswith("data models >"):
        name = section.split(">", 1)[1].strip()
        for amd in amds:
            for dm in amd.data_models:
                if dm.name.lower() == name.lower():
                    return render_data_model(dm)
        return None

    return None


# Prompt assembly


def assemble_task_prompt(
    task: PlanTask,
    config: OssatureConfig,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    final_outputs: list[str] | None = None,
) -> str:
    sections: list[str] = []

    # Project config
    config_lines = [
        f"Project: {config.name} v{config.version}",
        f"Language: {config.output.language}",
    ]
    if config.output.framework:
        config_lines.append(f"Framework: {config.output.framework}")
    sections.append("<project_config>\n" + "\n".join(config_lines) + "\n</project_config>")

    # Project brief
    brief_path = config.metadata_context_path / "project-brief.md"
    if brief_path.exists():
        sections.append(f"<project_brief>\n{brief_path.read_text().strip()}\n</project_brief>")

    # Spec brief
    spec_brief_path = config.metadata_context_spec_briefs_path / f"{task.spec}.md"
    if spec_brief_path.exists():
        sections.append(
            f'<spec_brief spec="{task.spec}">\n{spec_brief_path.read_text().strip()}\n</spec_brief>'
        )

    # Filtered spec sections (via spec_refs)
    smd = smd_map.get(task.spec)
    if smd and task.spec_refs:
        spec_parts: list[str] = []
        for ref in task.spec_refs:
            rendered = _render_spec_ref(smd, ref.strip())
            if rendered:
                spec_parts.append(rendered)
        if spec_parts:
            sections.append(
                "<specification_context>\n" + "\n\n".join(spec_parts) + "\n</specification_context>"
            )

    # Filtered arch sections (via arch_refs), plus the components this task
    # owns. Owned components are always included, whether or not the planner
    # listed them in arch_refs, so interfaces and contracts reach the
    # implementer deterministically rather than by planner choice.
    amds = amd_by_spec.get(task.spec)
    if amds:
        arch_parts: list[str] = []
        for ref in task.arch_refs:
            rendered = _render_arch_ref(amds, ref.strip())
            if rendered:
                arch_parts.append(rendered)
        # A component's contracts go to the implementer only for the files this
        # task finalizes. For a file a later task rewrites, the implementer still
        # sees the interface (so its placeholder stubs line up) but not the
        # behavioral contracts, which belong to the finalizing task.
        finalized = {
            _norm_rel_path(p) for p in (task.outputs if final_outputs is None else final_outputs)
        }
        for comp in components_for_paths(amds, task.outputs):
            rendered = render_component(
                comp, include_contracts=_norm_rel_path(comp.path) in finalized
            )
            if rendered not in arch_parts and not any(rendered in part for part in arch_parts):
                arch_parts.append(rendered)
        if arch_parts:
            sections.append(
                "<architecture_context>\n" + "\n\n".join(arch_parts) + "\n</architecture_context>"
            )

    # Inject files — list available dependency files for tool-based exploration.
    # Only file names are listed (no line counts or sizes) so the prompt text
    # stays stable when later tasks edit these files.
    if task.inject_files:
        available: list[str] = []
        for filepath in task.inject_files:
            full_path = config.output_path / filepath
            if full_path.exists():
                available.append(f"- `{filepath}`")
        if available:
            sections.append(
                "<dependency_files>\n"
                "The following files from previous tasks are available. "
                "Use `grep_file` and `read_lines` to inspect the types, "
                "interfaces, and signatures you need.\n\n"
                + "\n".join(available)
                + "\n</dependency_files>"
            )

    # Cross-spec interfaces
    if task.cross_spec_interfaces:
        iface_sections: list[str] = []
        for spec_id in task.cross_spec_interfaces:
            iface_path = config.metadata_context_interfaces_path / f"{spec_id}.md"
            if iface_path.exists():
                iface_sections.append(
                    f'<interface spec="{spec_id}">\n{iface_path.read_text().strip()}\n</interface>'
                )
        if iface_sections:
            sections.append(
                "<cross_spec_interfaces>\n"
                + "\n\n".join(iface_sections)
                + "\n</cross_spec_interfaces>"
            )

    # Context files
    if task.context_files:
        context_file_entries: list[str] = []
        for cf in task.context_files:
            cf_path = config.context_path / cf
            if not cf_path.exists():
                context_file_entries.append(f"- `{cf}` — not found")
                continue
            mime_type = content_types.get_content_type(cf_path.name) or "application/octet-stream"
            size = cf_path.stat().st_size
            is_text = mime_type.startswith("text/") or mime_type in {
                "application/json",
                "application/xml",
                "application/toml",
                "application/yaml",
            }
            if is_text:
                try:
                    content = cf_path.read_text()
                    context_file_entries.append(
                        f"### {cf}\n\n"
                        f"**MIME type:** `{mime_type}` ({size} bytes)\n\n"
                        f"```\n{content}\n```"
                    )
                except UnicodeDecodeError:
                    context_file_entries.append(
                        f"- `{cf}` — `{mime_type}` ({size} bytes) — "
                        f"use `read_context_file` or `copy_context_file` to access"
                    )
            else:
                context_file_entries.append(f"- `{cf}` — `{mime_type}` ({size} bytes)")

        if context_file_entries:
            sections.append(
                "<context_files>\n"
                "The following files from the project's context directory are assigned "
                "to this task. Use `copy_context_file(context_path, dest_path)` to copy "
                "assets into the appropriate location within the output directory "
                "(choose a destination path that fits the project structure, e.g. "
                "`assets/audio/music.mp3` or `sounds/correct.wav`). "
                "Use `read_context_file(context_path)` to read text files on demand.\n\n"
                + "\n\n".join(context_file_entries)
                + "\n</context_files>"
            )

    # Task description — placed last so the query follows all context
    task_block = f"<task>\n## {task.title}\n\n{task.description}"
    if task.notes:
        task_block += f"\n\n**Notes:** {task.notes}"
    if task.outputs:
        outputs_list = "\n".join(f"- `{o}`" for o in task.outputs)
        task_block += f"\n\n## Files to Produce\n\n{outputs_list}"
    task_block += "\n</task>"
    sections.append(task_block)

    return "\n\n".join(sections)


def assemble_fix_prompt(
    task: PlanTask, error_output: str, config: OssatureConfig, verify_command: str = ""
) -> str:
    sections = [f"<error_output>\n```\n{error_output}\n```\n</error_output>"]

    if verify_command:
        sections.append(f"<verify_command>\n{verify_command}\n</verify_command>")

    # Include output files, falling back to inject_files for modify-in-place tasks.
    # Files in task.outputs that don't exist on disk are filtered out upstream by
    # build_task, which short-circuits to a missing-outputs failure rather than
    # entering the fix loop. So here we only see files that actually exist (or
    # inject_files that may legitimately not exist for unusual modify-in-place flows).
    file_list = task.outputs if task.outputs else (task.inject_files or [])
    for filepath in file_list:
        full_path = config.output_path / filepath
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text()
        except UnicodeDecodeError:
            continue

        line_count = content.count("\n") + 1
        if line_count > config.build.max_inline_lines:
            sections.append(
                f'<current_file path="{filepath}" total_lines="{line_count}">\n'
                f"File is large. Use `read_lines` or `grep_file` to inspect "
                f"the regions referenced in the error output above.\n"
                f"</current_file>"
            )
        else:
            sections.append(
                f'<current_file path="{filepath}">\n```\n{content}\n```\n</current_file>'
            )

    sections.append(f"<task>\n**{task.title}**: {task.description}\n</task>")

    return "\n\n".join(sections)


def assemble_review_fix_prompt(
    task: PlanTask,
    report: ReviewReport,
    config: OssatureConfig,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    contract_paths: list[str] | None = None,
) -> str:
    """Format the reviewer's issues as the error input to the fixer, prefixed
    with the spec sections and contracts the code must satisfy.

    A verify failure is mechanical, so assemble_fix_prompt gives the fixer just
    the error and the current files. A review failure is about conformance, so
    the fixer also needs the requirements and contracts that define what correct
    means here, not only the reviewer's summary of the violation.
    """
    paths = task.outputs if contract_paths is None else contract_paths
    lines = [
        "The code compiled and passed verification, but a review against the "
        "specification and declared contracts found these problems. Fix each one "
        "without breaking the build:",
        "",
    ]
    for issue in report.issues:
        lines.append(f"- {issue.file} -- {issue.target}: {issue.problem}")
        if issue.suggestion:
            lines.append(f"  Fix: {issue.suggestion}")
    base = assemble_fix_prompt(task, "\n".join(lines), config)
    context = _conformance_context(task, smd_map, amd_by_spec, paths)
    if context:
        return "\n\n".join(context) + "\n\n" + base
    return base
