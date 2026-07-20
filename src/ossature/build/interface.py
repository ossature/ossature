"""Interface extraction from a completed spec's generated source."""

from pydantic_ai import Agent
from rich.console import Console
from rich.status import Status

from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.plan import Plan, TaskStatus
from ossature.promptspec import render
from ossature.shared.llm import UsageTracker, run_agent_sync


def extract_spec_interface(
    spec_id: str,
    plan: Plan,
    config: OssatureConfig,
    console: Console,
    status: Status,
    tracker: UsageTracker | None = None,
    amds: list[AMDSpec] | None = None,
) -> bool:
    """Extract and write the interface file for a spec.

    Returns True when an interface file was actually written, False when there
    was no extractable source to write (all outputs are copy assets, missing on
    disk, or unreadable). The caller relies on this to decide whether a freshly
    rebuilt upstream has a trustworthy interface on disk; a False return means
    the file on disk (if any) is stale and dependents must rebuild.
    """
    source_files: list[tuple[str, str]] = []
    for task in plan.tasks:
        if task.spec != spec_id or task.status != TaskStatus.DONE:
            continue
        if task.source or task.kind == "verify":
            # Copy tasks ship verbatim assets (often binary), and verify
            # tasks emit fixtures and harnesses. Neither has a
            # generated-source interface to extract.
            continue
        for filepath in task.outputs:
            full_path = config.output_path / filepath
            if not full_path.exists():
                continue
            try:
                source_files.append((filepath, full_path.read_text(encoding="utf-8")))
            except OSError, UnicodeDecodeError:
                continue

    if not source_files:
        return False

    language = config.output.language
    sections = [f"# Source files for {spec_id}\n"]
    for filepath, content in source_files:
        sections.append(f"## {filepath}\n\n```{language}\n{content}\n```\n")

    status.update(f"Extracting interface: {spec_id}")
    console.log(f"  [cyan]Extracting interface for {spec_id}...[/cyan]")

    model = config.llm.model_for("interface")
    agent = Agent(
        model,
        instructions=render("build.interface_extraction", language=language),
        retries={"output": config.llm.retries},
    )
    result = run_agent_sync(
        agent,
        "\n".join(sections),
        operation="interface extraction",
        model_name=model,
        spec_id=spec_id,
        tracker=tracker,
    )

    interface_content = f"# Interface: {spec_id}\n\n@source: build\n\n{result.output}"

    # Declared AMD contracts are appended deterministically, never through
    # the extraction LLM, so the boundary promises the author wrote cannot
    # be paraphrased away or rewritten to match a buggy implementation.
    if amds:
        contract_lines: list[str] = []
        for amd in amds:
            for comp in amd.components:
                if comp.contracts:
                    contract_lines.append(f"### {comp.name}")
                    contract_lines.append("")
                    for contract in comp.contracts:
                        contract_lines.append(f"- {contract}")
                    contract_lines.append("")
        if contract_lines:
            section = "\n".join(contract_lines).rstrip()
            interface_content += f"\n\n## Declared Contracts\n\n{section}\n"

    iface_dir = config.metadata_context_interfaces_path
    iface_dir.mkdir(parents=True, exist_ok=True)
    (iface_dir / f"{spec_id}.md").write_text(interface_content)

    console.log(f"  [green]Interface written: .ossature/context/interfaces/{spec_id}.md[/green]")
    return True
