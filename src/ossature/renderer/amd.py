from pathlib import Path

from ossature.models.amd import AMDSpec, Component, DataModel, Dependency


def render_component(component: Component, include_contracts: bool = True) -> str:
    lines = [
        f"### {component.name}",
        "",
        f"@path: {component.path}",
        "",
        component.description,
        "",
        "**Interface:**",
        "",
    ]

    if component.interface_language:
        lines.append(f"```{component.interface_language}")
    else:
        lines.append("```")

    lines.append(component.interface)
    lines.append("```")

    # The contracts marker is required by the parser, so an empty list renders
    # as an explicit 'None'. In prompt context the block can be omitted entirely
    # (include_contracts=False) for a component a task is only scaffolding, where
    # the behavioral guarantees do not apply yet.
    if include_contracts:
        lines.append("")
        if component.contracts:
            lines.append("**Contracts:**")
            lines.append("")
            for contract in component.contracts:
                lines.append(f"- {contract}")
        else:
            lines.append("**Contracts:** None")

    if component.depends_on:
        lines.append("")
        lines.append(f"**Depends on:** {', '.join(component.depends_on)}")

    return "\n".join(lines)


def render_data_model(model: DataModel) -> str:
    lines = [
        f"### {model.name}",
        "",
    ]

    if model.definition_language:
        lines.append(f"```{model.definition_language}")
    else:
        lines.append("```")

    lines.append(model.definition)
    lines.append("```")

    return "\n".join(lines)


def render_dependency(dependency: Dependency) -> str:
    return f"- {dependency.name}: {dependency.purpose}"


def render_amd(spec: AMDSpec) -> str:
    lines = [
        "---",
        f"spec: {spec.spec_id}",
        f"status: {spec.status.value}",
        "---",
        "",
        f"# Architecture: {spec.title}",
        "",
        "## Overview",
        "",
        spec.overview,
        "",
    ]

    if spec.components:
        lines.append("## Components")
        lines.append("")
        for component in spec.components:
            lines.append(render_component(component))
            lines.append("")

    if spec.data_models:
        lines.append("## Data Models")
        lines.append("")
        for model in spec.data_models:
            lines.append(render_data_model(model))
            lines.append("")

    if spec.flow:
        lines.append("## Flow")
        lines.append("")
        lines.append("```")
        lines.append(spec.flow)
        lines.append("```")
        lines.append("")

    if spec.dependencies:
        lines.append("## Dependencies")
        lines.append("")
        for dependency in spec.dependencies:
            lines.append(render_dependency(dependency))
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(spec.notes if spec.notes else "")

    return "\n".join(lines)


def save_amd(spec: AMDSpec, path: Path, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_amd(spec)
    path.write_text(content, encoding="utf-8")

    return path
