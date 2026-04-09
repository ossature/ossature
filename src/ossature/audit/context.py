from pydantic_ai import Agent

from ossature.config.loader import OssatureConfig
from ossature.models.audit import Brief
from ossature.models.smd import SMDSpec
from ossature.renderer.smd import render_smd
from ossature.shared.llm import UsageTracker, run_agent_sync


def format_smd_specs_overviews(specs: list[SMDSpec]) -> str:
    spec_map = {spec.spec_id: spec for spec in specs}

    visited = set()
    visiting = set()
    ordered_specs = []

    def visit(spec_id: str) -> None:
        if spec_id in visited:
            return
        if spec_id in visiting:
            raise ValueError(f"Circular dependency detected involving {spec_id}")

        visiting.add(spec_id)

        if spec_id in spec_map:
            spec = spec_map[spec_id]
            # Visit all dependencies first
            for dep_id in spec.depends:
                if dep_id in spec_map:
                    visit(dep_id)

            ordered_specs.append(spec)

        visiting.remove(spec_id)
        visited.add(spec_id)

    for spec in specs:
        visit(spec.spec_id)

    formatted_parts = []
    for spec in ordered_specs:
        dependency_line = f"**Priority:** {spec.priority.value} | **Spec ID:** {spec.spec_id}"
        if spec.depends:
            dependency_line += f" | **Depends on:** {', '.join(spec.depends)}"

        formatted_part = f"## {spec.title}\n\n{dependency_line}\n\n{spec.overview}"
        formatted_parts.append(formatted_part)

    return "\n\n".join(formatted_parts)


def generate_project_brief(
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    tracker: UsageTracker | None = None,
) -> Brief:
    system_prompt = (
        "<role>\n"
        "You are a technical writer creating a project summary for an LLM code generation system.\n"
        "</role>\n\n"
        "<instructions>\n"
        "Given the overview sections of all specs in a project, write a single paragraph "
        "(~200 words) that captures:\n"
        "- What the project does\n"
        "- The main modules/specs and their responsibilities\n"
        "- Key technologies and frameworks\n"
        "- How the modules connect\n\n"
        "Write in present tense, be concrete, avoid marketing language.\n"
        "This summary will be included in every code generation prompt "
        "to provide project context.\n"
        "</instructions>\n\n"
        "Output only the brief, no preamble."
    )

    overviews = format_smd_specs_overviews(parsed_smds)

    project_info = f"Project: {config.name} v{config.version} — Language: {config.output.language}"
    if config.output.framework:
        project_info += f" — Framework: {config.output.framework}"
    user_prompt = f"{project_info}\n\n{overviews}"

    model = config.llm.model_for("brief")
    agent = Agent(
        model,
        instructions=system_prompt,
        retries=config.llm.retries,
    )

    result = run_agent_sync(
        agent,
        user_prompt,
        operation="project brief generation",
        model_name=model,
        tracker=tracker,
    )

    return Brief(brief=result.output)


def generate_spec_briefs(
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    tracker: UsageTracker | None = None,
) -> dict[str, Brief]:
    system_prompt = (
        "<role>\n"
        "You are a technical writer creating a module "
        "summary for an LLM code generation system.\n"
        "</role>\n\n"
        "<instructions>\n"
        "Given a specification document (SMD), write 2-3 sentences that capture:\n"
        "- What this module does\n"
        "- Its key responsibilities\n"
        "- What it integrates with\n\n"
        "Be concrete and technical. This summary provides context during code "
        "generation for related modules.\n"
        "</instructions>\n\n"
        "Output only the brief, no preamble."
    )

    model = config.llm.model_for("brief")
    agent = Agent(
        model,
        instructions=system_prompt,
        retries=config.llm.retries,
    )

    briefs: dict[str, Brief] = {}

    for smd in parsed_smds:
        result = run_agent_sync(
            agent,
            render_smd(spec=smd),
            operation="spec brief generation",
            model_name=model,
            spec_id=smd.spec_id,
            tracker=tracker,
        )

        briefs[smd.spec_id] = Brief(brief=result.output)

    return briefs
