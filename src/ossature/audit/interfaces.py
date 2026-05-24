from pydantic_ai import Agent

from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.smd import SMDSpec
from ossature.promptspec import render
from ossature.renderer.smd import render_smd
from ossature.shared.llm import UsageTracker, run_agent_sync


def extract_interface_from_amds(
    spec_id: str,
    amds: list[AMDSpec],
    language: str,
) -> str:
    lines: list[str] = [
        f"# Interface: {spec_id}",
        "",
        "@source: amd",
        "",
    ]

    all_components = [comp for amd in amds for comp in amd.components]
    all_data_models = [dm for amd in amds for dm in amd.data_models]

    if all_components:
        lines.append("## Components")
        lines.append("")
        for comp in all_components:
            lines.append(f"### {comp.name}")
            lines.append("")
            lines.append(f"**Path:** `{comp.path}`")
            lines.append("")
            lines.append(comp.description)
            lines.append("")
            if comp.interface:
                lang = comp.interface_language or language
                lines.append(f"```{lang}")
                lines.append(comp.interface)
                lines.append("```")
                lines.append("")
            if comp.depends_on:
                lines.append(f"**Depends on:** {', '.join(comp.depends_on)}")
                lines.append("")

    if all_data_models:
        lines.append("## Data Models")
        lines.append("")
        for dm in all_data_models:
            lines.append(f"### {dm.name}")
            lines.append("")
            if dm.definition:
                lang = dm.definition_language or language
                lines.append(f"```{lang}")
                lines.append(dm.definition)
                lines.append("```")
                lines.append("")

    return "\n".join(lines)


def infer_interface_from_smd(
    config: OssatureConfig,
    smd: SMDSpec,
    dependency_interfaces: dict[str, str] | None = None,
    tracker: UsageTracker | None = None,
) -> str:
    model = config.llm.model_for("interface")
    agent = Agent(
        model,
        instructions=render("audit.interface_inference", language=config.output.language),
        retries=config.llm.retries,
    )

    sections: list[str] = [render_smd(smd)]

    if dependency_interfaces:
        sections.append("\n---\n")
        sections.append("## Dependency Interfaces\n")
        sections.append(
            "The following are the public interfaces of modules this spec depends on. "
            "Your proposed interface should be compatible with these.\n"
        )
        for _dep_id, interface in dependency_interfaces.items():
            sections.append(interface)
            sections.append("")

    result = run_agent_sync(
        agent,
        "\n".join(sections),
        operation="interface inference",
        model_name=model,
        spec_id=smd.spec_id,
        tracker=tracker,
    )

    return f"# Interface: {smd.spec_id}\n\n@source: llm\n\n{result.output}"


def propagate_to_smd_dependents(
    specs_needing_update: set[str],
    parsed_smds: list[SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
) -> set[str]:
    # When a dependency's interface changes, SMD-only dependents need
    # regeneration because their LLM inference may produce different
    # results with the updated dependency interface.

    # AMD-backed specs are not affected since their interface is
    # determined solely by AMD content.

    smd_only = {smd.spec_id for smd in parsed_smds if smd.spec_id not in amd_by_spec}
    result = set(specs_needing_update)

    changed = True
    while changed:
        changed = False
        for smd in parsed_smds:
            if (
                smd.spec_id in smd_only
                and smd.spec_id not in result
                and any(dep in result for dep in smd.depends)
            ):
                result.add(smd.spec_id)
                changed = True

    return result
