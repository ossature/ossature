from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai import Agent

from ossature.audit.prompts import (
    CROSS_SPEC_AUDIT_SYSTEM_PROMPT,
    SPEC_AUDIT_SYSTEM_PROMPT,
)
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.audit import CrossSpecAuditReport, SpecAuditReport
from ossature.models.smd import SMDSpec
from ossature.shared.llm import run_agent_sync


def _read_numbered(path: Path) -> str:
    """Read a file and prefix each line with its line number."""
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    numbered = [f"L{i}: {line}" for i, line in enumerate(lines, 1)]
    return "\n".join(numbered)


def audit_spec(
    config: OssatureConfig,
    smd_path: Path,
    spec_id: str,
    amd_paths: list[Path] | None = None,
) -> SpecAuditReport:
    model = config.llm.model_for("audit")
    agent = Agent(
        model,
        output_type=SpecAuditReport,
        system_prompt=SPEC_AUDIT_SYSTEM_PROMPT.format(language=config.output.language),
        retries=config.llm.retries,
    )

    sections: list[str] = []

    sections.append("# Specification (SMD)\n")
    sections.append(_read_numbered(smd_path))

    if amd_paths:
        sections.append("\n# Architecture Documents (AMD)\n")
        for amd_path in amd_paths:
            sections.append(_read_numbered(amd_path))

    result = run_agent_sync(
        agent,
        "\n---\n".join(sections),
        operation="spec audit",
        model_name=model,
        spec_id=spec_id,
    )

    return result.output


def audit_cross_specs(
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec] | None = None,
) -> CrossSpecAuditReport:
    """
    Audit interfaces between interdependent specs.
    Only meaningful when there are multiple specs with dependencies.
    """
    model = config.llm.model_for("audit")
    agent = Agent(
        model,
        output_type=CrossSpecAuditReport,
        system_prompt=CROSS_SPEC_AUDIT_SYSTEM_PROMPT.format(language=config.output.language),
        retries=config.llm.retries,
    )

    # Build dependency graph representation
    graph_lines = ["## Spec Dependency Graph\n"]
    for smd in parsed_smds:
        deps = ", ".join(smd.depends) if smd.depends else "(none)"
        graph_lines.append(f"- {smd.spec_id}: depends on [{deps}]")

    # Group AMDs by spec_id for lookup
    amd_by_spec: dict[str, list[AMDSpec]] = {}
    if parsed_amds:
        for amd in parsed_amds:
            amd_by_spec.setdefault(amd.spec_id, []).append(amd)

    # Build condensed spec summaries
    summary_lines = ["\n## Spec Summaries\n"]
    for smd in parsed_smds:
        summary_lines.append(f"### {smd.spec_id}: {smd.title}\n")
        summary_lines.append(f"**Overview:** {smd.overview}\n")

        if smd.requirements:
            summary_lines.append("**Requirements:**")
            for req in smd.requirements:
                summary_lines.append(f"- {req.title}")
            summary_lines.append("")

        # Include AMD details if available
        spec_amds = amd_by_spec.get(smd.spec_id, [])
        if spec_amds:
            # Components with interfaces
            all_components = [comp for amd in spec_amds for comp in amd.components]
            if all_components:
                summary_lines.append("**Components:**")
                for comp in all_components:
                    deps_str = (
                        f" [depends: {', '.join(comp.depends_on)}]" if comp.depends_on else ""
                    )
                    summary_lines.append(f"- {comp.name}: {comp.description}{deps_str}")
                    if comp.interface:
                        summary_lines.append(f"  ```{comp.interface_language}")
                        summary_lines.append(f"  {comp.interface}")
                        summary_lines.append("  ```")
                summary_lines.append("")

            # Data models (critical for cross-spec contracts)
            all_data_models = [dm for amd in spec_amds for dm in amd.data_models]
            if all_data_models:
                summary_lines.append("**Data Models:**")
                for dm in all_data_models:
                    summary_lines.append(f"- {dm.name}")
                    if dm.definition:
                        summary_lines.append(f"  ```{dm.definition_language}")
                        summary_lines.append("  {dm.definition}")
                        summary_lines.append("  ```")
                summary_lines.append("")

            # External dependencies
            all_deps = [dep for amd in spec_amds for dep in amd.dependencies]
            if all_deps:
                summary_lines.append("**External Dependencies:**")
                for dep in all_deps:
                    summary_lines.append(f"- {dep.name}: {dep.purpose}")
                summary_lines.append("")

    audit_input = "\n".join(graph_lines) + "\n" + "\n".join(summary_lines)

    result = run_agent_sync(
        agent,
        audit_input,
        operation="cross-spec audit",
        model_name=model,
    )

    return result.output


def save_spec_audit_data(report: SpecAuditReport, spec_id: str, audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    filepath = audit_dir / f"{spec_id}.json"
    filepath.write_text(report.model_dump_json(indent=2))


def load_spec_audit_data(spec_id: str, audit_dir: Path) -> SpecAuditReport | None:
    filepath = audit_dir / f"{spec_id}.json"
    if not filepath.exists():
        return None
    return SpecAuditReport.model_validate_json(filepath.read_text())


def save_cross_spec_audit_data(report: CrossSpecAuditReport, audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    filepath = audit_dir / "cross-spec.json"
    filepath.write_text(report.model_dump_json(indent=2))


def load_cross_spec_audit_data(audit_dir: Path) -> CrossSpecAuditReport | None:
    filepath = audit_dir / "cross-spec.json"
    if not filepath.exists():
        return None
    return CrossSpecAuditReport.model_validate_json(filepath.read_text())


def save_audit_report(
    spec_reports: dict[str, SpecAuditReport],
    cross_spec_report: CrossSpecAuditReport | None,
    name: str,
    filename: Path,
) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    with open(filename, "w") as f:
        f.write(f"# Audit Report: {name}\n\n")

        current_time_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write(f"**Date:** {current_time_utc}\n")
        f.write(f"**Specs:** {', '.join(spec_reports.keys())}\n\n")

        if cross_spec_report:
            f.write("## Cross-Spec Findings\n\n")
            if cross_spec_report.findings:
                for finding in cross_spec_report.findings:
                    location = " <-> ".join(finding.specs)
                    f.write(f"### {finding.severity.value.upper()}: {location}\n\n")
                    f.write(f"**Issue:** {finding.issue}\n\n")
                    f.write(f"**Suggestion:** {finding.suggestion}\n\n")
            else:
                f.write("No cross-spec findings identified.\n\n")

        for spec_id, report in spec_reports.items():
            f.write(f"## {spec_id} Findings\n\n")
            if report.findings:
                for spec_finding in report.findings:
                    f.write(
                        f"### {spec_finding.severity.value.upper()}: {spec_finding.location}\n\n"
                    )
                    f.write(f"**Issue:** {spec_finding.issue}\n\n")
                    f.write(f"**Suggestion:** {spec_finding.suggestion}\n\n")
            else:
                f.write("No findings identified.\n\n")
