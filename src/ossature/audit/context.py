import hashlib

from pydantic_ai import Agent

from ossature.config.loader import OssatureConfig
from ossature.models.audit import Brief
from ossature.models.smd import SMDSpec
from ossature.promptspec import render
from ossature.shared.hashing import HASH_ALGO
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


def _project_brief_user_prompt(config: OssatureConfig, parsed_smds: list[SMDSpec]) -> str:
    project_info = f"Project: {config.name} v{config.version} — Language: {config.output.language}"
    if config.output.framework:
        project_info += f" — Framework: {config.output.framework}"
    return f"{project_info}\n\n{format_smd_specs_overviews(parsed_smds)}"


def _spec_brief_user_prompt(smd: SMDSpec) -> str:
    deps = ", ".join(smd.depends) if smd.depends else "none"
    return (
        f"# {smd.title}\n\n"
        f"**Spec ID:** {smd.spec_id}\n"
        f"**Depends on:** {deps}\n\n"
        f"## Overview\n\n{smd.overview}"
    )


def _hash_brief_input(model: str, system_prompt: str, user_prompt: str) -> str:
    h = hashlib.new(HASH_ALGO)
    for part in (model, system_prompt, user_prompt):
        h.update(part.encode())
        h.update(b"\0")
    return f"{HASH_ALGO}:{h.hexdigest()}"


def compute_project_brief_input_hash(config: OssatureConfig, parsed_smds: list[SMDSpec]) -> str:
    return _hash_brief_input(
        config.llm.model_for("brief"),
        render("audit.project_brief"),
        _project_brief_user_prompt(config, parsed_smds),
    )


def compute_spec_brief_input_hash(config: OssatureConfig, smd: SMDSpec) -> str:
    return _hash_brief_input(
        config.llm.model_for("brief"),
        render("audit.spec_brief"),
        _spec_brief_user_prompt(smd),
    )


def generate_project_brief(
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    tracker: UsageTracker | None = None,
) -> Brief:
    model = config.llm.model_for("brief")
    agent = Agent(
        model,
        instructions=render("audit.project_brief"),
        retries={"output": config.llm.retries},
    )

    result = run_agent_sync(
        agent,
        _project_brief_user_prompt(config, parsed_smds),
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
    model = config.llm.model_for("brief")
    agent = Agent(
        model,
        instructions=render("audit.spec_brief"),
        retries={"output": config.llm.retries},
    )

    briefs: dict[str, Brief] = {}

    for smd in parsed_smds:
        result = run_agent_sync(
            agent,
            _spec_brief_user_prompt(smd),
            operation="spec brief generation",
            model_name=model,
            spec_id=smd.spec_id,
            tracker=tracker,
        )

        briefs[smd.spec_id] = Brief(brief=result.output)

    return briefs
