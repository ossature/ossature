from pathlib import Path

from ossature.models.smd import Example, Requirement, SMDSpec


def render_requirement(requirement: Requirement) -> str:
    lines = [
        f"### {requirement.title}",
        "",
        requirement.description,
        "",
        f"**Accepts:** {requirement.accepts}",
        "",
        f"**Returns:** {requirement.returns}",
    ]

    if requirement.errors:
        lines.append("")
        lines.append("**Errors:**")
        lines.append("")
        for condition, response in requirement.errors:
            lines.append(f"- {condition} -> {response}")

    return "\n".join(lines)


def render_example(example: Example) -> str:
    lines = [
        f"### {example.name}",
        "",
        "**Input:**",
        "",
        "```",
        example.input,
        "```",
        "",
        "**Output:**",
        "",
        "```",
        example.output,
        "```",
    ]
    return "\n".join(lines)


def render_smd(spec: SMDSpec) -> str:
    lines = [
        "---",
        f"id: {spec.spec_id}",
        f"status: {spec.status.value}",
        f"priority: {spec.priority.value}",
        f"depends: [{', '.join(spec.depends)}]",
        "---",
        "",
        f"# {spec.title}",
        "",
        "## Overview",
        "",
        spec.overview,
        "",
    ]

    if spec.goals:
        lines.append("## Goals")
        lines.append("")
        for goal in spec.goals:
            lines.append(f"- {goal}")
        lines.append("")

    if spec.non_goals:
        lines.append("## Non-Goals")
        lines.append("")
        for non_goal in spec.non_goals:
            lines.append(f"- {non_goal}")
        lines.append("")

    lines.append("## Requirements")
    lines.append("")
    for requirement in spec.requirements:
        lines.append(render_requirement(requirement))
        lines.append("")

    if spec.constraints:
        lines.append("## Constraints")
        lines.append("")
        for constraint in spec.constraints:
            lines.append(f"- {constraint}")
        lines.append("")

    if spec.examples:
        lines.append("## Examples")
        lines.append("")
        for example in spec.examples:
            lines.append(render_example(example))
            lines.append("")

    if spec.acceptance_criteria:
        lines.append("## Acceptance Criteria")
        lines.append("")
        for criterion in spec.acceptance_criteria:
            lines.append(f"- [ ] {criterion}")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(spec.notes if spec.notes else "")

    return "\n".join(lines)


def save_smd(spec: SMDSpec, path: Path, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_smd(spec)
    path.write_text(content, encoding="utf-8")

    return path


def save_smd_with_name(
    spec: SMDSpec,
    directory: Path,
    filename: str | None = None,
    overwrite: bool = False,
) -> Path:
    if filename is None:
        filename = spec.spec_id.lower().replace("_", "-")

    if not filename.endswith(".smd.md"):
        filename = f"{filename}.smd.md"

    path = directory / filename
    return save_smd(spec, path, overwrite=overwrite)
