from collections.abc import Callable
from pathlib import Path

import questionary
from rich import box
from rich.console import Console
from rich.markup import escape
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
from ossature.audit.context import (
    compute_project_brief_input_hash,
    compute_spec_brief_input_hash,
    generate_project_brief,
    generate_spec_briefs,
)
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
    Manifest,
    Severity,
    SpecAuditReport,
)
from ossature.models.smd import SMDSpec
from ossature.models.vmd import VMDSpec
from ossature.parsers.amd import AMDParseError, parse_amd_file
from ossature.parsers.smd import SMDParseError, parse_smd_file
from ossature.parsers.vmd import VMDParseError
from ossature.shared.llm import UsageTracker
from ossature.validation import ValidationError, validate_specs
from ossature.verification.tasks import synthesize_verify_tasks

FixMode = str  # "auto" | "interactive" | "none"


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
    counts = dict.fromkeys(Severity, 0)
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


def _has_fixable_errors(
    report: SpecAuditReport | CrossSpecAuditReport,
) -> bool:
    return any(f.severity == Severity.ERROR and f.suggestion for f in report.findings)


def _fixable_finding_count(
    report: SpecAuditReport | CrossSpecAuditReport,
) -> int:
    """Count fixable findings across all severities (for --interactive mode)."""
    return sum(1 for f in report.findings if f.suggestion)


def _confirm_or_abort(question: str, *, default: bool) -> bool:
    """Ask a yes/no question; ctrl-C aborts the audit instead of counting
    as a decline."""
    answer = questionary.confirm(question, default=default).ask()
    if answer is None:
        raise SystemExit(130)
    return bool(answer)


def _build_spec_file_map(
    smd_files: list[Path],
    parsed_smds: list[SMDSpec],
    spec_dir: Path,
) -> dict[str, str]:
    """Map spec_id -> relative file path within spec_dir."""
    result: dict[str, str] = {}
    for smd_file, smd in zip(smd_files, parsed_smds, strict=True):
        result[smd.spec_id] = str(smd_file.relative_to(spec_dir))
    return result


def _build_amd_file_map(
    amd_files: list[Path],
    parsed_amds: list[AMDSpec],
    spec_dir: Path,
) -> dict[str, list[str]]:
    """Map spec_id -> list of relative AMD file paths within spec_dir."""
    result: dict[str, list[str]] = {}
    for amd_file, amd in zip(amd_files, parsed_amds, strict=True):
        result.setdefault(amd.spec_id, []).append(str(amd_file.relative_to(spec_dir)))
    return result


def check_and_update_manifest(
    console: Console,
    config: OssatureConfig,
    smd_files: list[Path],
    amd_files: list[Path],
    vmd_files: list[Path] | None = None,
) -> tuple[list[str] | None, Manifest]:
    """Returns (changed source keys or None if unchanged, current manifest).

    Brief input hashes from a prior manifest are carried forward so brief
    regeneration can compare against them after fix cycles.
    """
    config.metadata_path.mkdir(parents=True, exist_ok=True)
    manifest_path = config.metadata_path / "manifest.toml"

    old: Manifest | None = None
    if manifest_path.exists():
        console.log("Reading existing manifest")
        old = read_manifest(manifest_path)
        if not old:
            console.log("Malformed manifest. Disregarding.")

    new_manifest = create_manifest(
        config=config,
        smd_files=smd_files,
        amd_files=amd_files,
        vmd_files=vmd_files,
        brief_inputs=old.brief_inputs if old else None,
        project_brief_input=old.project_brief_input if old else "",
    )

    if old is not None:
        mismatched = new_manifest.diff(other=old)
        if mismatched:
            console.log("[red]Manifest changed")
            for source in mismatched:
                console.log(f"  {source} has changed")
            write_manifest(new_manifest, filename=manifest_path)
            console.log("Manifest updated")
            return mismatched, new_manifest
        else:
            console.log("[green]Manifest unchanged")
            return None, new_manifest

    write_manifest(new_manifest, filename=manifest_path)
    console.log("[green]Manifest written")
    return list(new_manifest.sources.keys()), new_manifest


def get_changed_spec_ids(
    changed_files: list[str],
    smd_files: list[Path],
    amd_files: list[Path],
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    config: OssatureConfig,
    vmd_files: list[Path] | None = None,
    parsed_vmds: list[VMDSpec] | None = None,
) -> set[str]:
    """Maps changed manifest source keys to spec IDs."""
    if "ossature.toml" in changed_files:
        return {smd.spec_id for smd in parsed_smds}

    file_to_spec: dict[str, str] = {}

    for smd_file, smd in zip(smd_files, parsed_smds, strict=True):
        key = str(smd_file).replace(str(config.root), ".")
        file_to_spec[key] = smd.spec_id

    for amd_file, amd in zip(amd_files, parsed_amds, strict=True):
        key = str(amd_file).replace(str(config.root), ".")
        file_to_spec[key] = amd.spec_id

    for vmd_file, vmd in zip(vmd_files or [], parsed_vmds or [], strict=True):
        key = str(vmd_file).replace(str(config.root), ".")
        file_to_spec[key] = vmd.spec_id

    return {file_to_spec[f] for f in changed_files if f in file_to_spec}


def generate_and_write_briefs(
    console: Console,
    status: Status,
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    manifest: Manifest,
    tracker: UsageTracker | None = None,
) -> None:
    """Regenerate briefs whose narrowed LLM input has changed (or whose file is
    missing). Updates ``manifest`` in place with the latest input hashes so the
    caller can persist them.
    """
    config.metadata_context_path.mkdir(parents=True, exist_ok=True)
    config.metadata_context_spec_briefs_path.mkdir(parents=True, exist_ok=True)

    project_brief_filepath = config.metadata_context_path / "project-brief.md"
    project_hash = compute_project_brief_input_hash(config, parsed_smds)

    if manifest.project_brief_input == project_hash and project_brief_filepath.exists():
        console.log("Project brief unchanged")
    else:
        status.update("Generating project brief")
        project_brief = generate_project_brief(
            config=config, parsed_smds=parsed_smds, tracker=tracker
        )
        project_brief_filepath.write_text(project_brief.brief)
        manifest.project_brief_input = project_hash
        console.log(f"Project brief written to [bold]{project_brief_filepath}")

    smds_to_brief: list[SMDSpec] = []
    spec_hashes: dict[str, str] = {}
    for smd in parsed_smds:
        spec_hash = compute_spec_brief_input_hash(config, smd)
        spec_hashes[smd.spec_id] = spec_hash
        spec_brief_filepath = config.metadata_context_spec_briefs_path / f"{smd.spec_id}.md"
        if manifest.brief_inputs.get(smd.spec_id) == spec_hash and spec_brief_filepath.exists():
            continue
        smds_to_brief.append(smd)

    if not smds_to_brief:
        console.log("Spec briefs unchanged")
        status.stop()
        return

    status.update("Generating spec briefs")
    spec_briefs = generate_spec_briefs(config=config, parsed_smds=smds_to_brief, tracker=tracker)

    for spec_id, brief in spec_briefs.items():
        spec_brief_filepath = config.metadata_context_spec_briefs_path / f"{spec_id}.md"
        spec_brief_filepath.write_text(brief.brief)
        manifest.brief_inputs[spec_id] = spec_hashes[spec_id]
        console.log(f"Spec brief written to [bold]{spec_brief_filepath}")

    status.stop()


def generate_and_write_interfaces(
    console: Console,
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    changed_spec_ids: set[str],
    topo_levels: list[list[str]],
    tracker: UsageTracker | None = None,
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
                    config, smd, dep_interfaces if dep_interfaces else None, tracker=tracker
                )

            interfaces[spec_id] = interface

            filepath = config.metadata_context_interfaces_path / f"{spec_id}.md"
            with open(filepath, "w") as f:
                f.write(interface)
                f.flush()

            source = "AMD" if amds else "LLM"
            console.log(f"  Written to [bold]{filepath}[/bold] ({source})")


class _AuditRun:
    """State for one run_audit invocation: parsed specs, the derived file
    maps, and the reports the phases accumulate."""

    def __init__(
        self,
        config: OssatureConfig,
        console: Console,
        fix_mode: FixMode,
        replan: bool,
        interactive: bool,
        errors_ok: bool,
    ) -> None:
        self.config = config
        self.console = console
        self.fix_mode = fix_mode
        self.replan = replan
        self.interactive = interactive
        self.errors_ok = errors_ok

        self.audit_usage = UsageTracker()
        self.audit_data_dir = config.metadata_path / "audits"
        self.spec_reports: dict[str, SpecAuditReport] = {}
        self.audited_spec_ids: set[str] = set()
        self.cross_spec_report: CrossSpecAuditReport | None = None
        self.specs_to_audit: set[str] = set()
        self.specs_missing_interfaces: set[str] = set()

    def run(self) -> None:
        with Status("Spec validation", console=self.console) as status:
            if not self._load_and_validate():
                return
            self._determine_specs_to_audit(status)
            self._check_cached_artifacts(status)
            self._audit_specs(status)
            self._print_fresh_findings()
            self._audit_cross_specs(status)
            self._write_report()
            self._refresh_briefs_and_manifest(status)
            self._generate_interfaces(status)
            if not self._generate_plan(status):
                return
            self._print_usage()
            self._exit_on_errors()

    def _load_and_validate(self) -> bool:
        """Parse and validate all spec files, write the graph, and build the
        derived lookup maps. Returns False when there is nothing to audit."""
        config = self.config
        self.smd_files = list(config.spec_path.glob("**/*.smd"))
        self.amd_files = list(config.spec_path.glob("**/*.amd"))
        self.vmd_files = list(config.spec_path.glob("**/*.vmd"))

        if not self.smd_files:
            self.console.print("[yellow]No spec files found.[/]")
            return False

        try:
            self.parsed_smds, self.parsed_amds, self.parsed_vmds = validate_specs(
                self.smd_files, self.amd_files, self.vmd_files
            )
        except SMDParseError, AMDParseError, VMDParseError, ValidationError:
            self.console.log("[red] Specs invalid. Run `ossature validate` to see errors.")
            raise SystemExit(1) from None

        self.console.log("[green]✓ specs valid")

        for amd in self.parsed_amds:
            for warning in amd.warnings:
                self.console.log(f"[yellow]WARNING:[/] {escape(amd.spec_id)}: {escape(warning)}")

        self.graph = build_spec_graph(
            self.parsed_smds, self.parsed_amds, self.smd_files, self.amd_files, config.root
        )
        spec_graph_filepath = config.metadata_path / "graph.toml"
        write_spec_graph(self.graph, spec_graph_filepath)
        self.console.log(f"Spec graph written to [bold]{spec_graph_filepath}")

        self.amd_by_spec: dict[str, list[AMDSpec]] = {}
        for amd in self.parsed_amds:
            self.amd_by_spec.setdefault(amd.spec_id, []).append(amd)

        self.spec_file_map = _build_spec_file_map(
            self.smd_files, self.parsed_smds, config.spec_path
        )
        self.amd_file_map = _build_amd_file_map(self.amd_files, self.parsed_amds, config.spec_path)
        self.smd_path_map = {
            parsed.spec_id: smd_file
            for smd_file, parsed in zip(self.smd_files, self.parsed_smds, strict=True)
        }
        # Spec id -> its VMD files, read-only context for the audit agent
        self.vmd_file_map: dict[str, list[Path]] = {}
        for vmd_file, parsed_vmd in zip(self.vmd_files, self.parsed_vmds, strict=True):
            self.vmd_file_map.setdefault(parsed_vmd.spec_id, []).append(vmd_file)
        self.amd_index_by_rel = {
            str(f.relative_to(config.spec_path)): i for i, f in enumerate(self.amd_files)
        }
        return True

    def _determine_specs_to_audit(self, status: Status) -> None:
        changed_files, self.manifest = check_and_update_manifest(
            self.console, self.config, self.smd_files, self.amd_files, self.vmd_files
        )

        if changed_files is None:
            if self.interactive:
                status.stop()
                if _confirm_or_abort("Re-audit is not required. Re-audit anyway?", default=False):
                    self.specs_to_audit = {smd.spec_id for smd in self.parsed_smds}
                status.start()
            else:
                self.console.log("[green]No changes detected — skipping re-audit")
        else:
            self.specs_to_audit = get_changed_spec_ids(
                changed_files,
                self.smd_files,
                self.amd_files,
                self.parsed_smds,
                self.parsed_amds,
                self.config,
                vmd_files=self.vmd_files,
                parsed_vmds=self.parsed_vmds,
            )

    def _check_cached_artifacts(self, status: Status) -> None:
        """Force re-audit or re-interface for specs whose cached files are
        gone. Brief regeneration is gated by manifest hashes, handled later."""
        status.update("Checking cached artifacts")
        specs_missing_audit: set[str] = set()

        for smd in self.parsed_smds:
            if smd.spec_id not in self.specs_to_audit:
                audit_json = self.audit_data_dir / smd.spec_id / "response.json"
                if not audit_json.exists():
                    specs_missing_audit.add(smd.spec_id)

            interface_file = self.config.metadata_context_interfaces_path / f"{smd.spec_id}.md"
            if not interface_file.exists():
                self.specs_missing_interfaces.add(smd.spec_id)

        if specs_missing_audit:
            self.console.log(
                f"[yellow]Missing audit data for: {', '.join(sorted(specs_missing_audit))}. "
                "Will re-audit."
            )
            self.specs_to_audit |= specs_missing_audit

        if self.specs_missing_interfaces:
            self.console.log(
                f"[yellow]Missing interfaces for: "
                f"{', '.join(sorted(self.specs_missing_interfaces))}. "
                "Will regenerate."
            )

    def _run_fix_cycle[ReportT: (SpecAuditReport, CrossSpecAuditReport)](
        self,
        status: Status,
        *,
        status_text: Callable[[int], str],
        audit_once: Callable[[], ReportT],
        log_label: str,
        title: str,
        confirm_text: Callable[[int], str],
        fix_once: Callable[[ReportT], list[str]],
        fixing_status: str,
        fixed_label: str,
        no_edits_message: str,
        on_fixed: Callable[[list[str]], None],
    ) -> ReportT:
        """Audit, then fix and re-audit until clean, declined, or out of
        cycles. Shared by the per-spec and cross-spec audits; the callables
        carry what differs. Returns the last report."""
        max_cycles = self.config.audit.max_fix_cycles
        for fix_cycle in range(max_cycles + 1):
            status.update(status_text(fix_cycle))
            report = audit_once()

            counts = dict.fromkeys(Severity, 0)
            for finding in report.findings:
                counts[finding.severity] += 1
            summary = ", ".join(f"{v} {k.value}(s)" for k, v in counts.items() if v > 0)
            self.console.log(f"  {log_label}: {summary or 'no findings'}")

            if self.fix_mode == "none" or fix_cycle >= max_cycles:
                return report

            if self.fix_mode == "auto":
                if not _has_fixable_errors(report):
                    return report
            else:  # interactive
                if not report.findings or not _fixable_finding_count(report):
                    return report

                print_audit_summary(self.console, report=report, title=title)
                print_audit_findings_table(self.console, report=report)

                fixable = _fixable_finding_count(report)
                status.stop()
                if not _confirm_or_abort(confirm_text(fixable), default=True):
                    status.start()
                    return report
                status.start()

            status.update(fixing_status)
            edited = fix_once(report)
            if not edited:
                self.console.log(no_edits_message)
                return report

            self.console.log(
                f"  [green]Fixed {len(edited)} file(s) for {fixed_label} — re-auditing[/green]"
            )
            on_fixed(edited)
        raise RuntimeError("Unreachable")

    def _audit_specs(self, status: Status) -> None:
        for smd_idx, smd in enumerate(self.parsed_smds):
            if smd.spec_id in self.specs_to_audit:
                self._audit_one_spec(smd_idx, smd, status)
            else:
                cached = load_spec_audit_data(smd.spec_id, self.audit_data_dir)
                if cached:
                    self.spec_reports[smd.spec_id] = cached
                    self.console.log(f"  {smd.spec_id} - {smd.title}: [dim](cached)[/dim]")

    def _audit_one_spec(self, smd_idx: int, smd: SMDSpec, status: Status) -> None:
        spec_id = smd.spec_id
        title = smd.title
        spec_file = self.spec_file_map[spec_id]

        def audit_once() -> SpecAuditReport:
            report = audit_spec(
                self.config,
                self.smd_path_map[spec_id],
                spec_id,
                [self.config.spec_path / rel for rel in self.amd_file_map.get(spec_id, [])] or None,
                vmd_paths=self.vmd_file_map.get(spec_id),
                tracker=self.audit_usage,
                transcript_dir=self.audit_data_dir / spec_id,
            )
            save_spec_audit_data(report, spec_id, self.audit_data_dir)
            self.spec_reports[spec_id] = report
            self.audited_spec_ids.add(spec_id)
            return report

        def fix_once(report: SpecAuditReport) -> list[str]:
            return fix_spec_findings(
                findings=report.findings,
                spec_file=spec_file,
                spec_dir=self.config.spec_path,
                config=self.config,
                console=self.console,
                status=status,
                tracker=self.audit_usage,
                amd_files=self.amd_file_map.get(spec_id, []),
            )

        def on_fixed(edited: list[str]) -> None:
            # Re-parse the edited spec, updating the shared list so
            # cross-spec audit, briefs, interfaces, and plan generation
            # see the fixed content
            self.parsed_smds[smd_idx] = parse_smd_file(self.config.spec_path / spec_file)
            amd_rel_files = self.amd_file_map.get(spec_id, [])
            if any(f in edited for f in amd_rel_files):
                spec_amds = [parse_amd_file(self.config.spec_path / af) for af in amd_rel_files]
                self.amd_by_spec[spec_id] = spec_amds
                for af, new_amd in zip(amd_rel_files, spec_amds, strict=True):
                    self.parsed_amds[self.amd_index_by_rel[af]] = new_amd

        self._run_fix_cycle(
            status,
            status_text=lambda cycle: (
                f"Auditing {spec_id} - {title}"
                if cycle == 0
                else f"Re-auditing {spec_id} (cycle {cycle + 1})"
            ),
            audit_once=audit_once,
            log_label=spec_id,
            title=f"{spec_id} Audit",
            confirm_text=lambda fixable: f"Auto-fix {fixable} finding(s) in {spec_id}?",
            fix_once=fix_once,
            fixing_status=f"Fixing {spec_id} findings",
            fixed_label=spec_id,
            no_edits_message=f"  [yellow]No edits made for {spec_id}[/yellow]",
            on_fixed=on_fixed,
        )

    def _print_fresh_findings(self) -> None:
        if not self.audited_spec_ids:
            return
        fresh_findings = SpecAuditReport(
            findings=[
                f
                for sid in self.audited_spec_ids
                if sid in self.spec_reports
                for f in self.spec_reports[sid].findings
            ]
        )

        print_audit_summary(
            self.console,
            report=fresh_findings,
            title=f"{self.config.name} v{self.config.version} - Spec Audit",
        )

        if fresh_findings.findings:
            print_audit_findings_table(self.console, report=fresh_findings)

    def _audit_cross_specs(self, status: Status) -> None:
        if len(self.parsed_smds) <= 1:
            return
        if not self.audited_spec_ids:
            self.cross_spec_report = load_cross_spec_audit_data(self.audit_data_dir)
            return

        def audit_once() -> CrossSpecAuditReport:
            report = audit_cross_specs(
                self.config,
                self.parsed_smds,
                self.parsed_amds,
                tracker=self.audit_usage,
                transcript_dir=self.audit_data_dir / "cross-spec",
            )
            save_cross_spec_audit_data(report, self.audit_data_dir)
            return report

        def fix_once(report: CrossSpecAuditReport) -> list[str]:
            return fix_cross_spec_findings(
                findings=report.findings,
                spec_files=self.spec_file_map,
                spec_dir=self.config.spec_path,
                config=self.config,
                console=self.console,
                status=status,
                tracker=self.audit_usage,
            )

        def on_fixed(edited: list[str]) -> None:
            for smd_idx, smd_obj in enumerate(self.parsed_smds):
                rel = self.spec_file_map.get(smd_obj.spec_id, "")
                if rel in edited:
                    self.parsed_smds[smd_idx] = parse_smd_file(self.config.spec_path / rel)

        project = f"{self.config.name} v{self.config.version}"
        self.cross_spec_report = self._run_fix_cycle(
            status,
            status_text=lambda cycle: (
                f"Cross-spec audit - {project}"
                if cycle == 0
                else f"Re-running cross-spec audit (cycle {cycle + 1})"
            ),
            audit_once=audit_once,
            log_label="Cross-spec",
            title=f"{project} - Cross-Spec Audit",
            confirm_text=lambda fixable: f"Auto-fix {fixable} cross-spec finding(s)?",
            fix_once=fix_once,
            fixing_status="Fixing cross-spec findings",
            fixed_label="cross-spec findings",
            no_edits_message="[yellow]No edits made for cross-spec findings[/yellow]",
            on_fixed=on_fixed,
        )

        if self.cross_spec_report and self.cross_spec_report.findings:
            print_audit_summary(
                self.console,
                report=self.cross_spec_report,
                title=f"{project} - Cross-Spec Audit",
            )
            print_audit_findings_table(self.console, report=self.cross_spec_report)

    def _write_report(self) -> None:
        if not self.spec_reports:
            return
        audit_report_filepath = self.config.metadata_path / "audit-report.md"
        save_audit_report(
            spec_reports=self.spec_reports,
            cross_spec_report=self.cross_spec_report,
            name=f"{self.config.name} v{self.config.version}",
            filename=audit_report_filepath,
        )
        self.console.log(f"Audit report saved to [bold]{audit_report_filepath}")

    def _refresh_briefs_and_manifest(self, status: Status) -> None:
        # Briefs run before the manifest write so updated input hashes persist
        generate_and_write_briefs(
            self.console,
            status,
            self.config,
            self.parsed_smds,
            manifest=self.manifest,
            tracker=self.audit_usage,
        )

        # Refresh the manifest: source checksums may have changed during fix
        # cycles, and brief_inputs were just updated in place
        self.manifest = create_manifest(
            config=self.config,
            smd_files=self.smd_files,
            amd_files=self.amd_files,
            vmd_files=self.vmd_files,
            brief_inputs=self.manifest.brief_inputs,
            project_brief_input=self.manifest.project_brief_input,
        )
        write_manifest(self.manifest, filename=self.config.metadata_path / "manifest.toml")

    def _generate_interfaces(self, status: Status) -> None:
        specs_needing_interfaces = propagate_to_smd_dependents(
            self.audited_spec_ids | self.specs_missing_interfaces,
            self.parsed_smds,
            self.amd_by_spec,
        )

        if not specs_needing_interfaces:
            self.console.log("[yellow]Interface regeneration not required")
            return

        status.update("Generating interfaces")
        status.start()
        generate_and_write_interfaces(
            self.console,
            self.config,
            self.parsed_smds,
            self.amd_by_spec,
            changed_spec_ids=specs_needing_interfaces,
            topo_levels=self.graph.levels,
            tracker=self.audit_usage,
        )
        status.stop()

    def _generate_plan(self, status: Status) -> bool:
        """Generate or refresh plan.toml. Returns False when the user
        declined a replan, which aborts the rest of the run."""
        plan_filepath = self.config.metadata_path / "plan.toml"
        needs_plan = bool(self.audited_spec_ids) or not plan_filepath.exists() or self.replan

        if self.replan and plan_filepath.exists():
            if self.interactive:
                status.stop()
                if not _confirm_or_abort(
                    "This will overwrite the existing plan (discarding manual edits). Continue?",
                    default=False,
                ):
                    self.console.print("[yellow]Plan regeneration skipped.")
                    return False
                status.start()
            else:
                self.console.log("[yellow]--replan: overwriting existing plan")

        if not needs_plan:
            self.console.log("[yellow]Plan regeneration not required")
            return True

        status.update("Generating build plan")
        status.start()

        # Load the existing plan for incremental merge (unless --replan
        # forces a full regen)
        existing_plan = load_plan(plan_filepath) if not self.replan else None
        use_incremental = existing_plan is not None and bool(self.audited_spec_ids)

        # Deterministic verify tasks from the author-written VMDs; the
        # LLM planner never sees or emits these.
        verify_tasks_by_spec, verify_warnings = synthesize_verify_tasks(
            self.config,
            list(zip(self.vmd_files, self.parsed_vmds, strict=True)),
            self.amd_by_spec,
        )
        for warning in verify_warnings:
            self.console.log(f"[yellow]WARNING:[/] {escape(warning)}")

        plan, id_remap, matched_old_ids = generate_plan(
            config=self.config,
            parsed_smds=self.parsed_smds,
            amd_by_spec=self.amd_by_spec,
            graph=self.graph,
            spec_reports=self.spec_reports,
            changed_spec_ids=self.audited_spec_ids if use_incremental else None,
            existing_plan=existing_plan if use_incremental else None,
            tracker=self.audit_usage,
            verify_tasks_by_spec=verify_tasks_by_spec,
        )

        # Remap task directories and build state if incremental merge happened
        if id_remap is not None and existing_plan is not None:
            tasks_dir = self.config.metadata_path / "tasks"
            remap_task_directories(
                tasks_dir, id_remap, self.audited_spec_ids, existing_plan, matched_old_ids
            )
            state_filepath = self.config.metadata_path / "state.toml"
            remap_build_state(
                state_filepath, id_remap, self.audited_spec_ids, existing_plan, matched_old_ids
            )

            # Clean up output files from old tasks that no longer exist in the new plan
            orphaned = collect_orphaned_output_files(existing_plan, plan, self.audited_spec_ids)
            if orphaned:
                removed = remove_orphaned_output_files(orphaned, self.config.output_path)
                if removed:
                    for f in removed:
                        self.console.log(f"  [dim]Removed stale output: {f}[/dim]")
                    self.console.log(
                        f"[yellow]Removed {len(removed)} stale output file(s) "
                        f"from previous plan[/yellow]"
                    )

            preserved = sum(1 for t in plan.tasks if t.spec not in self.audited_spec_ids)
            replanned = sum(1 for t in plan.tasks if t.spec in self.audited_spec_ids)
            self.console.log(
                f"Incremental re-plan: {preserved} task(s) preserved, "
                f"{replanned} task(s) re-planned"
            )

        write_plan(plan, plan_filepath)
        self.console.log(f"Build plan written to [bold]{plan_filepath}")

        status.stop()

        self.console.print()
        self.console.print(
            Panel(
                f"[bold]{plan.meta.total_tasks}[/bold] tasks planned across "
                f"[bold]{len(plan.meta.specs)}[/bold] spec(s)",
                title=f"[bold]{self.config.name} v{self.config.version} — Build Plan[/bold]",
                expand=False,
                box=box.ROUNDED,
            )
        )
        self.console.print()
        self.console.print(f"  Review the plan:  [cyan]{plan_filepath}[/cyan]")
        self.console.print("  Start building:   [cyan]ossature build[/cyan]")
        self.console.print()
        return True

    def _print_usage(self) -> None:
        if self.audit_usage.requests > 0:
            self.console.print(f"  [dim]LLM usage: {self.audit_usage.format_usage()}[/dim]")
            self.console.print()

    def _exit_on_errors(self) -> None:
        """Exit 1 if audit errors remain (unless --errors-ok)."""
        if self.errors_ok:
            return
        error_count = sum(
            1
            for r in self.spec_reports.values()
            for f in r.findings
            if f.severity == Severity.ERROR
        )
        if self.cross_spec_report:
            error_count += sum(
                1 for f in self.cross_spec_report.findings if f.severity == Severity.ERROR
            )
        if error_count:
            self.console.print(
                f"[red]Audit completed with {error_count} unresolved error(s).[/red]"
            )
            raise SystemExit(1)


@requires_llm
def run_audit(
    config_path: Path,
    console: Console,
    replan: bool = False,
    interactive: bool = False,
    no_fix: bool = False,
    errors_ok: bool = False,
) -> None:
    fix_mode: FixMode = "interactive" if interactive else ("none" if no_fix else "auto")
    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    _AuditRun(
        config,
        console,
        fix_mode,
        replan=replan,
        interactive=interactive,
        errors_ok=errors_ok,
    ).run()
