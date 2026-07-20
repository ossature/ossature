from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ossature.config.loader import ConfigError, OssatureConfig, load_config
from ossature.models.amd import AMDSpec
from ossature.models.shared import Status
from ossature.models.smd import Priority, SMDSpec
from ossature.models.vmd import VMDSpec
from ossature.parsers.amd import parse_amd_file
from ossature.parsers.smd import parse_smd_file
from ossature.parsers.vmd import parse_vmd_file
from ossature.validation import (
    MAX_REQUIREMENT_COMPLEXITY,
    ValidationError,
    cross_check_specs,
    find_vmd_target_issues,
    requirement_complexity,
    validate_specs,
)

__all__ = ["ValidationError", "run_validate", "validate_specs"]

STATUS_STYLE: dict[Status, str] = {
    Status.DRAFT: "dim",
    Status.REVIEW: "yellow",
    Status.APPROVED: "green",
    Status.IMPLEMENTED: "cyan",
    Status.DEPRECATED: "red",
}

PRIORITY_STYLE: dict[Priority, str] = {
    Priority.CRITICAL: "bold red",
    Priority.HIGH: "yellow",
    Priority.MEDIUM: "blue",
    Priority.LOW: "dim",
}


def print_validation_summary(
    console: Console,
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    parsed_vmds: list[VMDSpec] | None = None,
) -> None:
    parsed_vmds = parsed_vmds or []
    summary = (
        f"[green]✓[/green] Validated [bold]{len(parsed_smds)}[/bold] SMD(s) · "
        f"[bold]{len(parsed_amds)}[/bold] AMD(s)"
    )
    if parsed_vmds:
        summary += f" · [bold]{len(parsed_vmds)}[/bold] VMD(s)"
    console.print()
    console.print(
        Panel(
            summary,
            title="Validation Summary",
            border_style="green",
        )
    )

    if parsed_smds:
        console.print()
        tbl = Table(title="Specifications (SMD)", expand=False)
        tbl.add_column("Spec ID", style="bold cyan", no_wrap=True)
        tbl.add_column("Title")
        tbl.add_column("Status", justify="center")
        tbl.add_column("Priority", justify="center")
        tbl.add_column("Reqs", justify="right")
        tbl.add_column("Depends On", style="dim")

        for smd in parsed_smds:
            ss = STATUS_STYLE.get(smd.status, "")
            ps = PRIORITY_STYLE.get(smd.priority, "")
            deps = ", ".join(smd.depends) if smd.depends else "—"
            tbl.add_row(
                smd.spec_id,
                smd.title,
                f"[{ss}]{smd.status.value}[/{ss}]",
                f"[{ps}]{smd.priority.value}[/{ps}]",
                str(len(smd.requirements)),
                deps,
            )

        console.print(tbl)

    if parsed_amds:
        console.print()
        tbl = Table(title="Architecture (AMD)", expand=False)
        tbl.add_column("Spec ID", style="bold magenta", no_wrap=True)
        tbl.add_column("Title")
        tbl.add_column("Status", justify="center")
        tbl.add_column("Components", justify="right")
        tbl.add_column("Data Models", justify="right")

        for amd in parsed_amds:
            ss = STATUS_STYLE.get(amd.status, "")
            tbl.add_row(
                amd.spec_id,
                amd.title,
                f"[{ss}]{amd.status.value}[/{ss}]",
                str(len(amd.components)),
                str(len(amd.data_models)),
            )

        console.print(tbl)

    if parsed_vmds:
        console.print()
        tbl = Table(title="Verification (VMD)", expand=False)
        tbl.add_column("Spec ID", style="bold green", no_wrap=True)
        tbl.add_column("Status", justify="center")
        tbl.add_column("Groups", justify="right")
        tbl.add_column("Scenarios", justify="right")
        tbl.add_column("Cases", justify="right")

        for vmd in parsed_vmds:
            ss = STATUS_STYLE.get(vmd.status, "")
            case_count = sum(len(g.cases) for g in vmd.groups) + len(vmd.scenarios)
            tbl.add_row(
                vmd.spec_id,
                f"[{ss}]{vmd.status.value}[/{ss}]",
                str(len(vmd.groups)),
                str(len(vmd.scenarios)),
                str(case_count),
            )

        console.print(tbl)


def _warn_complex_specs(console: Console, parsed_smds: list[SMDSpec]) -> None:
    for smd in parsed_smds:
        complexity = requirement_complexity(smd)
        if complexity > MAX_REQUIREMENT_COMPLEXITY:
            console.print(
                f"\n[yellow]WARNING:[/] {smd.spec_id} has high requirement complexity. "
                f"Complex specs may fail during planning.\n"
                f"Consider splitting into multiple specs linked with `depends`."
            )


def warn_amd_parse_issues(console: Console, parsed_amds: list[AMDSpec]) -> None:
    for amd in parsed_amds:
        for warning in amd.warnings:
            console.print(f"\n[yellow]WARNING:[/] {escape(amd.spec_id)}: {escape(warning)}")


def warn_vmd_target_issues(
    console: Console,
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    parsed_vmds: list[VMDSpec],
) -> None:
    for issue in find_vmd_target_issues(parsed_smds, parsed_amds, parsed_vmds):
        console.print(f"\n[yellow]WARNING:[/] {escape(issue)}")


def report_coverage(
    console: Console,
    config: OssatureConfig,
    parsed_smds: list[SMDSpec],
    parsed_vmds: list[VMDSpec],
) -> None:
    """Print the requirement coverage ledger and its findings.

    Runs only when the project has VMD files: a project without verification
    specs should not be nagged about uncovered requirements. Uncovered
    requirements are warnings unless [test] require_coverage is set, which
    turns them into a validation failure.
    """
    if not parsed_vmds:
        return

    from ossature.verification.ledger import build_coverage_ledger, format_coverage_issues

    plan = None
    plan_path = config.metadata_path / "plan.toml"
    if plan_path.exists():
        from ossature.audit.planner import load_plan

        plan = load_plan(plan_path)

    ledger = build_coverage_ledger(parsed_smds, parsed_vmds, plan)

    if ledger.entries:
        console.print()
        tbl = Table(title="Requirement Coverage", expand=False)
        tbl.add_column("Spec ID", style="bold cyan", no_wrap=True)
        tbl.add_column("Requirement")
        tbl.add_column("Covered By")
        tbl.add_column("Errors", justify="center")

        for entry in ledger.entries:
            if entry.exempt:
                covered_by = "[dim]exempt (.no-verify)[/dim]"
            elif entry.covered:
                covered_by = ", ".join(entry.groups + entry.tasks)
            else:
                covered_by = "[red]uncovered[/red]"
            if entry.declared_error_types:
                errors_cell = f"{len(entry.covered_error_types)}/{len(entry.declared_error_types)}"
            else:
                errors_cell = "—"
            tbl.add_row(entry.spec_id, entry.title, covered_by, errors_cell)

        console.print(tbl)

    issues = format_coverage_issues(ledger)
    for issue in issues.advisory:
        console.print(f"\n[yellow]WARNING:[/] {escape(issue)}")

    if issues.uncovered:
        if config.test.require_coverage:
            for issue in issues.uncovered:
                console.print(f"\n[red]ERROR:[/] {escape(issue)}")
            console.print(
                "\n[red]Validation failed:[/] uncovered requirements with "
                "[test] require_coverage enabled."
            )
            raise SystemExit(1)
        for issue in issues.uncovered:
            console.print(f"\n[yellow]WARNING:[/] {escape(issue)}")


def run_validate(
    config_path: Path,
    verbose: bool,
    console: Console,
) -> None:
    from ossature.parsers.amd import AMDParseError
    from ossature.parsers.smd import SMDParseError
    from ossature.parsers.vmd import VMDParseError

    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))
    vmd_files = list(config.spec_path.glob("**/*.vmd"))

    if not smd_files:
        console.print("[yellow]No spec files found.[/]")
        return

    if not verbose:
        try:
            parsed_smds, parsed_amds, parsed_vmds = validate_specs(smd_files, amd_files, vmd_files)
        except (SMDParseError, AMDParseError, VMDParseError, ValidationError) as e:
            console.print(f"[red]Validation Error:[/] {e}")
            raise SystemExit(1) from None

        console.print()
        console.print("[green]✓[/green] All checks passed")
        print_validation_summary(
            console,
            parsed_smds=parsed_smds,
            parsed_amds=parsed_amds,
            parsed_vmds=parsed_vmds,
        )
        _warn_complex_specs(console, parsed_smds)
        warn_amd_parse_issues(console, parsed_amds)
        warn_vmd_target_issues(console, parsed_smds, parsed_amds, parsed_vmds)
        report_coverage(console, config, parsed_smds, parsed_vmds)
        return

    # Verbose path: show per-file progress, then delegate cross-reference checks
    console.print(f"Validating {len(smd_files)} SMD(s)")

    parsed_smds = []
    for smd_file in smd_files:
        smd_filename = str(smd_file).replace(str(config.root), ".")
        console.print(f" {smd_filename} ", end="")
        try:
            parsed_smds.append(parse_smd_file(smd_file))
            console.print("[green]✓")
        except SMDParseError as e:
            console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            for error in e.errors:
                console.print(f"  - {error}")
            raise SystemExit(1) from None

    console.print()
    console.print(f"Validating {len(amd_files)} AMD(s)")

    parsed_amds = []
    for amd_file in amd_files:
        amd_filename = str(amd_file).replace(str(config.root), ".")
        console.print(f" {amd_filename} ", end="")
        try:
            parsed_amds.append(parse_amd_file(amd_file))
            console.print("[green]✓")
        except AMDParseError as e:
            console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            for error in e.errors:
                console.print(f"  - {error}")
            raise SystemExit(1) from None

    console.print()
    console.print(f"Validating {len(vmd_files)} VMD(s)")

    parsed_vmds = []
    for vmd_file in vmd_files:
        vmd_filename = str(vmd_file).replace(str(config.root), ".")
        console.print(f" {vmd_filename} ", end="")
        try:
            parsed_vmds.append(parse_vmd_file(vmd_file))
            console.print("[green]✓")
        except VMDParseError as e:
            console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            for error in e.errors:
                console.print(f"  - {error}")
            raise SystemExit(1) from None

    # Cross-reference and cycle checks run on the specs already parsed above,
    # so the verbose path never parses a file twice.
    console.print()
    console.print("Cross-reference checks: ", end="")
    try:
        cross_check_specs(parsed_smds, parsed_amds, parsed_vmds)
        console.print("[green]✓ all checks passed")
    except ValidationError as e:
        console.print("[red]x")
        console.print(f" {e}")
        raise SystemExit(1) from None

    print_validation_summary(
        console,
        parsed_smds=parsed_smds,
        parsed_amds=parsed_amds,
        parsed_vmds=parsed_vmds,
    )
    _warn_complex_specs(console, parsed_smds)
    warn_amd_parse_issues(console, parsed_amds)
    warn_vmd_target_issues(console, parsed_smds, parsed_amds, parsed_vmds)
    report_coverage(console, config, parsed_smds, parsed_vmds)
