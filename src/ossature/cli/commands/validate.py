from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ossature.config.loader import ConfigError, load_config
from ossature.models.amd import AMDSpec
from ossature.models.shared import Status
from ossature.models.smd import Priority, SMDSpec
from ossature.parsers.amd import AMDParseError, parse_amd_file
from ossature.parsers.smd import SMDParseError, parse_smd_file

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
) -> None:
    console.print()
    console.print(
        Panel(
            f"[green]✓[/green] Validated [bold]{len(parsed_smds)}[/bold] SMD(s) · "
            f"[bold]{len(parsed_amds)}[/bold] AMD(s)",
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


def run_validate(
    config_path: Path,
    verbose: bool,
    console: Console,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    _conf_file = config.root / "ossature.toml"
    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))

    if not smd_files:
        console.print("[yellow]No spec files found.[/]")
        return

    if verbose:
        console.print(f"Validating {len(smd_files)} SMD(s)")

    parsed_smds = []
    parsed_amds = []

    for smd_file in smd_files:
        smd_filename = str(smd_file).replace(str(config.root), ".")
        if verbose:
            console.print(f" {smd_filename} ", end="")
        try:
            smd = parse_smd_file(smd_file)
            parsed_smds.append(smd)

            if verbose:
                console.print("[green]✓")
        except SMDParseError as e:
            if verbose:
                console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            else:
                console.print(smd_filename)
                console.print(f"[red]Validation Error:[/] {len(e.errors)} error(s)")

            for error in e.errors:
                console.print(f"  - {error}")

            raise SystemExit(1) from None

    if verbose:
        console.print()
        console.print(f"Validating {len(amd_files)} AMD(s)")

    for amd_file in amd_files:
        amd_filename = str(amd_file).replace(str(config.root), ".")
        if verbose:
            console.print(f" {amd_filename} ", end="")
        try:
            amd = parse_amd_file(amd_file)
            parsed_amds.append(amd)

            if verbose:
                console.print("[green]✓")
        except AMDParseError as e:
            if verbose:
                console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            else:
                console.print(smd_filename)
                console.print(f"[red]Validation Error:[/] {len(e.errors)} error(s)")

            for error in e.errors:
                console.print(f"  - {error}")

            raise SystemExit(1) from None

    console.print()
    console.print("Cross-reference spec dependencies: ", end="")

    # Cross reference parsed spec ids
    smd_spec_ids = [smd.spec_id for smd in parsed_smds]

    for smd in parsed_smds:
        for dep in smd.depends:
            if dep not in smd_spec_ids:
                console.print("[red]x")
                console.print(f" Spec {smd.spec_id} has dependency {dep} that doesn't exist.")
                raise SystemExit(1)

    console.print("[green]✓ all dependency spec IDs resolve")

    # Cross reference architecture specs
    if parsed_amds:
        console.print()
        console.print("Cross-reference architecture for specs: ", end="")
        for amd in parsed_amds:
            if amd.spec_id not in smd_spec_ids:
                console.print("[red]x")
                console.print(f" Architecture for a spec {amd.spec_id} that doesn't exist.")
                raise SystemExit(1)

    console.print("[green]✓ spec ids resolve")

    # Summary
    print_validation_summary(console, parsed_smds=parsed_smds, parsed_amds=parsed_amds)
