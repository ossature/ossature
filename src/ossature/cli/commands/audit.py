from pathlib import Path

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from ossature.audit.audit import (
    audit_cross_specs,
    audit_spec,
    load_cross_spec_audit_data,
    load_spec_audit_data,
    save_audit_report,
    save_cross_spec_audit_data,
    save_spec_audit_data,
)
from ossature.audit.context import generate_project_brief, generate_spec_briefs
from ossature.audit.fixer import fix_cross_spec_findings, fix_spec_findings
from ossature.audit.graph import build_spec_graph, write_spec_graph
from ossature.audit.interfaces import (
    extract_interface_from_amds,
    infer_interface_from_smd,
    propagate_to_smd_dependents,
)
from ossature.audit.manifest import create_manifest, read_manifest, write_manifest
from ossature.audit.planner import (
    collect_orphaned_output_files,
    generate_plan,
    load_plan,
    remap_build_state,
    remap_task_directories,
    remove_orphaned_output_files,
    write_plan,
)
from ossature.cli.decorators import requires_llm
from ossature.config.loader import ConfigError, OssatureConfig, load_config
from ossature.models.amd import AMDSpec
from ossature.models.audit import (
    AuditFinding,
    CrossSpecAuditReport,
    CrossSpecFinding,
    Severity,
    SpecAuditReport,
)
from ossature.models.smd import SMDSpec
from ossature.parsers.amd import AMDParseError, parse_amd_file
from ossature.parsers.smd import SMDParseError, parse_smd_file


class ValidationError(Exception): ...


SEVERITY_STYLES: dict[Severity, tuple[str, str]] = {
    Severity.ERROR: ("red", "ERROR"),
    Severity.WARNING: ("yellow", "WARNING"),
    Severity.INFO: ("cyan", "INFO"),
}


def print_audit_summary(
    console: Console,
    report: SpecAuditReport | CrossSpecAuditReport,
    title: str = "Spec Audit Report",
) -> None:
    counts = {s: 0 for s in Severity}
    for finding in report.findings:
        counts[finding.severity] += 1

    summary = Text()
    for severity, (style, label) in SEVERITY_STYLES.items():
        summary.append(f"  {label}: {counts[severity]}  ", style=f"bold {style}")

    console.print()
    console.print(Panel(summary, title=f"[bold]{title}[/bold]", expand=False, box=box.ROUNDED))


def print_audit_findings_table(
    console: Console, report: SpecAuditReport | CrossSpecAuditReport
) -> None:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=True,
        expand=True,
        header_style="bold white",
    )

    table.add_column("Severity", style="bold", width=10, no_wrap=True)

    if isinstance(report, SpecAuditReport):
        table.add_column("Location", style="dim", width=20)
    else:
        table.add_column("Specs", style="dim", width=20)

    table.add_column("Issue", ratio=2)
    table.add_column("Suggestion", style="italic", ratio=3)

    severity_order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}
    sorted_findings: list[AuditFinding | CrossSpecFinding] = sorted(
        report.findings, key=lambda x: severity_order[x.severity]
    )

    for finding in sorted_findings:
        style, label = SEVERITY_STYLES[finding.severity]
        location = (
            finding.location if isinstance(finding, AuditFinding) else "-".join(finding.specs)
        )
        table.add_row(
            Text(label, style=f"bold {style}"),
            Text(location),
            Text(finding.issue),
            Text(finding.suggestion),
        )

    console.print(table)
    console.print()


def present_findings_and_confirm(
    console: Console,
    status: Status,
    report: SpecAuditReport | CrossSpecAuditReport,
) -> None:

    if not report.findings:
        return

    counts = {s: 0 for s in Severity}
    for finding in report.findings:
        counts[finding.severity] += 1

    status.stop()

    if questionary.confirm("Print full report?").ask():
        print_audit_findings_table(console, report=report)

    some_errors = counts[Severity.ERROR] > 0

    if (
        some_errors
        and not questionary.confirm(
            f"Audit found {counts[Severity.ERROR]} error(s). Continue?", default=False
        ).ask()
    ):
        raise SystemExit(1)

    status.start()


MAX_FIX_CYCLES = 3


def _has_fixable_findings(
    report: SpecAuditReport | CrossSpecAuditReport,
) -> bool:
    return any(f.suggestion for f in report.findings)


def _fixable_finding_count(
    report: SpecAuditReport | CrossSpecAuditReport,
) -> int:
    # Skip info-level findings when there are errors or warnings.
    has_errors_or_warnings = any(
        f.severity in (Severity.ERROR, Severity.WARNING) for f in report.findings
    )
    return sum(
        1
        for f in report.findings
        if f.suggestion and not (f.severity == Severity.INFO and has_errors_or_warnings)
    )


def _build_spec_file_map(
    smd_files: list[Path],
    amd_files: list[Path],
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    spec_dir: Path,
) -> dict[str, str]:
    """Map spec_id -> relative file path within spec_dir."""
    result: dict[str, str] = {}
    for smd_file, smd in zip(smd_files, parsed_smds):
        result[smd.spec_id] = str(smd_file.relative_to(spec_dir))
    return result


def _build_amd_file_map(
    amd_files: list[Path],
    parsed_amds: list[AMDSpec],
    spec_dir: Path,
) -> dict[str, list[str]]:
    """Map spec_id -> list of relative AMD file paths within spec_dir."""
    result: dict[str, list[str]] = {}
    for amd_file, amd in zip(amd_files, parsed_amds):
        result.setdefault(amd.spec_id, []).append(str(amd_file.relative_to(spec_dir)))
    return result


def check_and_update_manifest(
    console: Console,
    config: OssatureConfig,
    smd_files: list[Path],
    amd_files: list[Path],
) -> list[str] | None:
    """Returns changed source keys, or None if manifest unchanged."""
    config.metadata_path.mkdir(parents=True, exist_ok=True)
    manifest_path = config.metadata_path / "manifest.toml"
    new_manifest = create_manifest(config=config, smd_files=smd_files, amd_files=amd_files)

    if manifest_path.exists():
        console.log("Reading existing manifest")
        manifest = read_manifest(manifest_path)

        if not manifest:
            console.log("Malformed manifest. Disregarding.")
        else:
            mismatched = new_manifest.diff(other=manifest)

            if mismatched:
                console.log("[red]Manifest changed")
                for source in mismatched:
                    console.log(f"  {source} has changed")
                write_manifest(new_manifest, filename=manifest_path)
                console.log("Manifest updated")
                return mismatched
            else:
                console.log("[green]Manifest unchanged")
                return None

    write_manifest(new_manifest, filename=manifest_path)
    console.log("[green]Manifest written")
    return list(new_manifest.sources.keys())


def get_changed_spec_ids(
    changed_files: list[str],
    smd_files: list[Path],
    amd_files: list[Path],
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    config: OssatureConfig,
) -> set[str]:
    """Maps changed manifest source keys to spec IDs."""
    if "ossature.toml" in changed_files:
        return {smd.spec_id for smd in parsed_smds}

    file_to_spec: dict[str, str] = {}

    for smd_file, smd in zip(smd_files, parsed_smds):
        key = str(smd_file).replace(str(config.root), ".")
        file_to_spec[key] = smd.spec_id

    for amd_file, amd in zip(amd_files, parsed_amds):
        key = str(amd_file).replace(str(config.root), ".")
        file_to_spec[key] = amd.spec_id

    return {file_to_spec[f] for f in changed_files if f in file_to_spec}


def generate_and_write_briefs(
    console: Console,
    status: Status,
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    changed_spec_ids: set[str] | None = None,
) -> None:
    config.metadata_context_path.mkdir(parents=True, exist_ok=True)
    project_brief_filepath = config.metadata_context_path / "project-brief.md"

    # Only regenerate the project brief when it doesn't exist or all specs changed.
    # LLM-generated briefs produce slightly different text each run, which would
    # invalidate input hashes for every task during incremental re-plans.
    all_spec_ids = {s.spec_id for s in parsed_smds}
    is_full_audit = changed_spec_ids is None or changed_spec_ids == all_spec_ids

    if is_full_audit or not project_brief_filepath.exists():
        status.update("Generating project brief")
        project_brief = generate_project_brief(config=config, parsed_smds=parsed_smds)
        with open(project_brief_filepath, "w") as f:
            f.write(project_brief.brief)
            f.flush()
        console.log(f"Project brief written to [bold]{project_brief_filepath}")
    else:
        console.log("Project brief unchanged (incremental audit)")

    status.update("Generating spec briefs")
    smds_to_brief = (
        [s for s in parsed_smds if s.spec_id in changed_spec_ids]
        if changed_spec_ids is not None
        else parsed_smds
    )
    spec_brefs = generate_spec_briefs(config=config, parsed_smds=smds_to_brief)
    config.metadata_context_spec_briefs_path.mkdir(parents=True, exist_ok=True)

    for spec_id, brief in spec_brefs.items():
        spec_brief_filepath = config.metadata_context_spec_briefs_path / f"{spec_id}.md"
        with open(spec_brief_filepath, "w") as f:
            f.write(brief.brief)
            f.flush()
        console.log(f"Spec brief written to [bold]{spec_brief_filepath}")

    status.stop()


def generate_and_write_interfaces(
    console: Console,
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    changed_spec_ids: set[str],
    topo_levels: list[list[str]],
) -> None:
    config.metadata_context_interfaces_path.mkdir(parents=True, exist_ok=True)

    # Load cached interfaces for unchanged specs (needed as dependency context)
    interfaces: dict[str, str] = {}
    for smd in parsed_smds:
        if smd.spec_id not in changed_spec_ids:
            cached = config.metadata_context_interfaces_path / f"{smd.spec_id}.md"
            if cached.exists():
                interfaces[smd.spec_id] = cached.read_text()

    smd_map = {smd.spec_id: smd for smd in parsed_smds}

    for level in topo_levels:
        for spec_id in level:
            if spec_id not in changed_spec_ids:
                continue

            smd = smd_map[spec_id]
            amds = amd_by_spec.get(spec_id)

            if amds:
                console.log(f"Extracting interface for {spec_id} (from AMD)")
                interface = extract_interface_from_amds(spec_id, amds, config.output.language)
            else:
                console.log(f"Inferring interface for {spec_id} (from SMD)")
                dep_interfaces = {
                    dep_id: interfaces[dep_id] for dep_id in smd.depends if dep_id in interfaces
                }
                interface = infer_interface_from_smd(
                    config, smd, dep_interfaces if dep_interfaces else None
                )

            interfaces[spec_id] = interface

            filepath = config.metadata_context_interfaces_path / f"{spec_id}.md"
            with open(filepath, "w") as f:
                f.write(interface)
                f.flush()

            source = "AMD" if amds else "LLM"
            console.log(f"  Written to [bold]{filepath}[/bold] ({source})")


def quick_validate(
    smd_files: list[Path], amd_files: list[Path]
) -> tuple[list[SMDSpec], list[AMDSpec]]:
    parsed_smds: list[SMDSpec] = []
    parsed_amds: list[AMDSpec] = []

    for smd_file in smd_files:
        parsed_smds.append(parse_smd_file(smd_file))

    for amd_file in amd_files:
        parsed_amds.append(parse_amd_file(amd_file))

    smd_spec_ids = [smd.spec_id for smd in parsed_smds]

    for smd in parsed_smds:
        for dep in smd.depends:
            if dep not in smd_spec_ids:
                raise ValidationError

    if parsed_amds:
        for amd in parsed_amds:
            if amd.spec_id not in smd_spec_ids:
                raise ValidationError

    return parsed_smds, parsed_amds


@requires_llm
def run_audit(
    config_path: Path,
    verbose: bool,
    console: Console,
    replan: bool = False,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1)

    with Status("Spec validation", console=console) as status:
        # Quick validation
        smd_files = list(config.spec_path.glob("**/*.smd"))
        amd_files = list(config.spec_path.glob("**/*.amd"))

        if not smd_files:
            console.print("[yellow]No spec files found.[/]")
            return

        try:
            parsed_smds, parsed_amds = quick_validate(smd_files=smd_files, amd_files=amd_files)
        except SMDParseError, AMDParseError, ValidationError:
            console.log("[red] Specs invalid. Run: `ossature validate` to check errors.")
            raise SystemExit(1)

        console.log("[green]✓ specs valid")

        # Generate graph.toml
        graph = build_spec_graph(parsed_smds, parsed_amds, smd_files, amd_files, config.root)
        spec_graph_filepath = config.metadata_path / "graph.toml"
        write_spec_graph(graph, spec_graph_filepath)
        console.log(f"Spec graph written to [bold]{spec_graph_filepath}")

        # Check manifest for changes
        changed_files = check_and_update_manifest(console, config, smd_files, amd_files)

        if changed_files is None:
            status.stop()
            if questionary.confirm(
                "Re-audit is not required. Re-audit anyway?", default=False
            ).ask():
                specs_to_audit = {smd.spec_id for smd in parsed_smds}
            else:
                specs_to_audit = set()
            status.start()
        else:
            specs_to_audit = get_changed_spec_ids(
                changed_files, smd_files, amd_files, parsed_smds, parsed_amds, config
            )

        # Group AMDs by spec
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        for amd in parsed_amds:
            amd_by_spec.setdefault(amd.spec_id, []).append(amd)

        status.update("Checking cached artifacts")
        # Check for missing cached files — force re-audit/re-brief/re-interface if absent
        audit_data_dir = config.metadata_path / "audits"
        specs_missing_audit: set[str] = set()
        specs_missing_briefs: set[str] = set()
        specs_missing_interfaces: set[str] = set()

        for smd in parsed_smds:
            if smd.spec_id not in specs_to_audit:
                audit_json = audit_data_dir / f"{smd.spec_id}.json"
                if not audit_json.exists():
                    specs_missing_audit.add(smd.spec_id)

            brief_file = config.metadata_context_spec_briefs_path / f"{smd.spec_id}.md"
            if not brief_file.exists():
                specs_missing_briefs.add(smd.spec_id)

            interface_file = config.metadata_context_interfaces_path / f"{smd.spec_id}.md"
            if not interface_file.exists():
                specs_missing_interfaces.add(smd.spec_id)

        if specs_missing_audit:
            console.log(
                f"[yellow]Missing audit data for: {', '.join(sorted(specs_missing_audit))}. "
                "Will re-audit."
            )
            specs_to_audit |= specs_missing_audit

        project_brief_file = config.metadata_context_path / "project-brief.md"
        if not project_brief_file.exists():
            specs_missing_briefs |= {smd.spec_id for smd in parsed_smds}

        if specs_missing_briefs - specs_to_audit:
            console.log(
                f"[yellow]Missing briefs for: "
                f"{', '.join(sorted(specs_missing_briefs - specs_to_audit))}. "
                "Will regenerate."
            )

        if specs_missing_interfaces:
            console.log(
                f"[yellow]Missing interfaces for: "
                f"{', '.join(sorted(specs_missing_interfaces))}. "
                "Will regenerate."
            )

        # - PER-SPEC AUDIT
        spec_reports: dict[str, SpecAuditReport] = {}
        audited_spec_ids: set[str] = set()
        spec_file_map = _build_spec_file_map(
            smd_files, amd_files, parsed_smds, parsed_amds, config.spec_path
        )
        amd_file_map = _build_amd_file_map(amd_files, parsed_amds, config.spec_path)

        # Build spec_id -> absolute SMD path mapping
        smd_path_map: dict[str, Path] = {}
        for smd_file, parsed in zip(smd_files, parsed_smds):
            smd_path_map[parsed.spec_id] = smd_file

        for smd in parsed_smds:
            spec_amds = amd_by_spec.get(smd.spec_id)

            if smd.spec_id in specs_to_audit:
                spec_file = spec_file_map[smd.spec_id]

                for fix_cycle in range(MAX_FIX_CYCLES + 1):
                    if fix_cycle > 0:
                        status.update(f"Re-auditing {smd.spec_id} (cycle {fix_cycle + 1})")
                    else:
                        status.update(f"Auditing {smd.spec_id} - {smd.title}")

                    smd_path = smd_path_map[smd.spec_id]
                    spec_amd_paths = [
                        config.spec_path / rel for rel in amd_file_map.get(smd.spec_id, [])
                    ]
                    report = audit_spec(config, smd_path, spec_amd_paths or None)
                    save_spec_audit_data(report, smd.spec_id, audit_data_dir)
                    spec_reports[smd.spec_id] = report
                    audited_spec_ids.add(smd.spec_id)

                    counts = {s: 0 for s in Severity}
                    for finding in report.findings:
                        counts[finding.severity] += 1
                    summary = ", ".join(f"{v} {k.value}(s)" for k, v in counts.items() if v > 0)
                    console.log(f"  {smd.spec_id}: {summary or 'no findings'}")

                    if not report.findings or not _has_fixable_findings(report):
                        break
                    if fix_cycle >= MAX_FIX_CYCLES:
                        break

                    print_audit_summary(
                        console,
                        report=report,
                        title=f"{smd.spec_id} Audit",
                    )
                    present_findings_and_confirm(console, status, report)

                    fixable = _fixable_finding_count(report)
                    status.stop()
                    if not questionary.confirm(
                        f"Auto-fix {fixable} finding(s) in {smd.spec_id}?"
                        + (
                            " (info-level findings deferred until errors/warnings are resolved)"
                            if fixable < len(report.findings)
                            else ""
                        ),
                        default=False,
                    ).ask():
                        status.start()
                        break
                    status.start()

                    status.update(f"Fixing {smd.spec_id} findings")
                    edited = fix_spec_findings(
                        findings=report.findings,
                        spec_file=spec_file,
                        spec_dir=config.spec_path,
                        config=config,
                        console=console,
                        status=status,
                    )

                    if not edited:
                        console.log(f"  [yellow]No edits made for {smd.spec_id}[/yellow]")
                        break

                    console.log(
                        f"  [green]Fixed {len(edited)} file(s) for {smd.spec_id} "
                        f"— re-auditing[/green]"
                    )
                    # Re-parse the edited spec
                    smd_path = config.spec_path / spec_file
                    smd = parse_smd_file(smd_path)
                    # Re-parse AMDs if any were edited
                    amd_file_map = _build_amd_file_map(amd_files, parsed_amds, config.spec_path)
                    amd_rel_files = amd_file_map.get(smd.spec_id, [])
                    if any(f in edited for f in amd_rel_files):
                        spec_amds = [parse_amd_file(config.spec_path / af) for af in amd_rel_files]
                        amd_by_spec[smd.spec_id] = spec_amds
            else:
                cached = load_spec_audit_data(smd.spec_id, audit_data_dir)
                if cached:
                    spec_reports[smd.spec_id] = cached
                    console.log(f"  {smd.spec_id} - {smd.title}: [dim](cached)[/dim]")

        # Present findings for freshly audited specs
        if audited_spec_ids:
            fresh_findings = SpecAuditReport(
                findings=[
                    f
                    for sid in audited_spec_ids
                    if sid in spec_reports
                    for f in spec_reports[sid].findings
                ]
            )

            print_audit_summary(
                console,
                report=fresh_findings,
                title=f"{config.name} v{config.version} - Spec Audit",
            )

        # - CROSS-SPEC AUDIT
        cross_spec_report: CrossSpecAuditReport | None = None

        if len(parsed_smds) > 1:
            if audited_spec_ids:
                for fix_cycle in range(MAX_FIX_CYCLES + 1):
                    if fix_cycle > 0:
                        status.update(f"Re-running cross-spec audit (cycle {fix_cycle + 1})")
                    else:
                        status.update(f"Cross-spec audit - {config.name} v{config.version}")

                    cross_spec_report = audit_cross_specs(config, parsed_smds, parsed_amds)
                    save_cross_spec_audit_data(cross_spec_report, audit_data_dir)

                    print_audit_summary(
                        console,
                        report=cross_spec_report,
                        title=f"{config.name} v{config.version} - Cross-Spec Audit",
                    )

                    if not cross_spec_report.findings or not _has_fixable_findings(
                        cross_spec_report
                    ):
                        break
                    if fix_cycle >= MAX_FIX_CYCLES:
                        break

                    present_findings_and_confirm(console, status, cross_spec_report)

                    fixable = _fixable_finding_count(cross_spec_report)
                    status.stop()
                    if not questionary.confirm(
                        f"Auto-fix {fixable} cross-spec finding(s)?"
                        + (
                            " (info-level findings deferred until errors/warnings are resolved)"
                            if fixable < len(cross_spec_report.findings)
                            else ""
                        ),
                        default=False,
                    ).ask():
                        status.start()
                        break
                    status.start()

                    status.update("Fixing cross-spec findings")
                    edited = fix_cross_spec_findings(
                        findings=cross_spec_report.findings,
                        spec_files=spec_file_map,
                        spec_dir=config.spec_path,
                        config=config,
                        console=console,
                        status=status,
                    )

                    if not edited:
                        console.log("[yellow]No edits made for cross-spec findings[/yellow]")
                        break

                    console.log(
                        f"  [green]Fixed {len(edited)} file(s) for cross-spec findings "
                        f"— re-auditing[/green]"
                    )
                    # Re-parse any edited spec files
                    for smd_idx, smd_obj in enumerate(parsed_smds):
                        rel = spec_file_map.get(smd_obj.spec_id, "")
                        if rel in edited:
                            parsed_smds[smd_idx] = parse_smd_file(config.spec_path / rel)
            else:
                cross_spec_report = load_cross_spec_audit_data(audit_data_dir)

        # - WRITE UNIFIED AUDIT REPORT
        if spec_reports:
            audit_report_filepath = config.metadata_path / "audit-report.md"
            save_audit_report(
                spec_reports=spec_reports,
                cross_spec_report=cross_spec_report,
                name=f"{config.name} v{config.version}",
                filename=audit_report_filepath,
            )
            console.log(f"Audit report saved to [bold]{audit_report_filepath}")

        # Update manifest if spec files were edited during fix cycles
        if audited_spec_ids:
            manifest_path = config.metadata_path / "manifest.toml"
            updated_manifest = create_manifest(
                config=config, smd_files=smd_files, amd_files=amd_files
            )
            write_manifest(updated_manifest, filename=manifest_path)

        # - BRIEFS GENERATION
        specs_needing_briefs = audited_spec_ids | specs_missing_briefs
        if specs_needing_briefs:
            generate_and_write_briefs(
                console, status, config, parsed_smds, changed_spec_ids=specs_needing_briefs
            )
        else:
            console.log("[yellow]Project and spec brief regeneration not required")

        # - GENERATE INTERFACES
        specs_needing_interfaces = audited_spec_ids | specs_missing_interfaces
        specs_needing_interfaces = propagate_to_smd_dependents(
            specs_needing_interfaces, parsed_smds, amd_by_spec
        )

        if specs_needing_interfaces:
            status.update("Generating interfaces")
            status.start()
            generate_and_write_interfaces(
                console,
                config,
                parsed_smds,
                amd_by_spec,
                changed_spec_ids=specs_needing_interfaces,
                topo_levels=graph.levels,
            )
            status.stop()
        else:
            console.log("[yellow]Interface regeneration not required")

        # - GENERATE BUILD PLAN
        plan_filepath = config.metadata_path / "plan.toml"
        needs_plan = bool(audited_spec_ids) or not plan_filepath.exists() or replan

        if replan and plan_filepath.exists():
            status.stop()
            if not questionary.confirm(
                "This will overwrite the existing plan (discarding manual edits). Continue?",
                default=False,
            ).ask():
                console.print("[yellow]Plan regeneration skipped.")
                return
            status.start()

        if not needs_plan:
            console.log("[yellow]Plan regeneration not required")
        else:
            status.update("Generating build plan")
            status.start()

            # Load existing plan for incremental merge (unless --replan forces full regen)
            existing_plan = load_plan(plan_filepath) if not replan else None
            use_incremental = (
                existing_plan is not None
                and bool(audited_spec_ids)
                and audited_spec_ids != {smd.spec_id for smd in parsed_smds}
            )

            plan, id_remap = generate_plan(
                config=config,
                parsed_smds=parsed_smds,
                amd_by_spec=amd_by_spec,
                graph=graph,
                spec_reports=spec_reports,
                changed_spec_ids=audited_spec_ids if use_incremental else None,
                existing_plan=existing_plan if use_incremental else None,
            )

            # Remap task directories and build state if incremental merge happened
            if id_remap is not None and existing_plan is not None:
                tasks_dir = config.metadata_path / "tasks"
                remap_task_directories(tasks_dir, id_remap, audited_spec_ids, existing_plan)
                state_filepath = config.metadata_path / "state.toml"
                remap_build_state(state_filepath, id_remap, audited_spec_ids, existing_plan)

                # Clean up output files from old tasks that no longer exist in the new plan
                orphaned = collect_orphaned_output_files(existing_plan, plan, audited_spec_ids)
                if orphaned:
                    removed = remove_orphaned_output_files(orphaned, config.output_path)
                    if removed:
                        for f in removed:
                            console.log(f"  [dim]Removed stale output: {f}[/dim]")
                        console.log(
                            f"[yellow]Removed {len(removed)} stale output file(s) "
                            f"from previous plan[/yellow]"
                        )

                preserved = sum(1 for t in plan.tasks if t.spec not in audited_spec_ids)
                replanned = sum(1 for t in plan.tasks if t.spec in audited_spec_ids)
                console.log(
                    f"Incremental re-plan: {preserved} task(s) preserved, "
                    f"{replanned} task(s) re-planned"
                )

            write_plan(plan, plan_filepath)
            console.log(f"Build plan written to [bold]{plan_filepath}")

            status.stop()

            console.print()
            console.print(
                Panel(
                    f"[bold]{plan.meta.total_tasks}[/bold] tasks planned across "
                    f"[bold]{len(plan.meta.specs)}[/bold] spec(s)",
                    title=f"[bold]{config.name} v{config.version} — Build Plan[/bold]",
                    expand=False,
                    box=box.ROUNDED,
                )
            )
            console.print()
            console.print(f"  Review the plan:  [cyan]{plan_filepath}[/cyan]")
            console.print("  Start building:   [cyan]ossature build[/cyan]")
            console.print()
