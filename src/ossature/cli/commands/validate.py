from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ossature.config.loader import ConfigError, load_config
from ossature.models.amd import AMDSpec
from ossature.models.shared import Status
from ossature.models.smd import Priority, SMDSpec
from ossature.parsers.amd import parse_amd_file
from ossature.parsers.smd import parse_smd_file

MAX_REQUIREMENT_COMPLEXITY = 3000

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


class ValidationError(Exception):
    pass


_WHITE, _GRAY, _BLACK = 0, 1, 2


def _detect_cycle(dep_map: dict[str, list[str]]) -> list[str] | None:
    color: dict[str, int] = dict.fromkeys(dep_map, _WHITE)
    parent: dict[str, str | None] = dict.fromkeys(dep_map, None)

    def dfs(node: str) -> list[str] | None:
        color[node] = _GRAY
        for dep in dep_map.get(node, []):
            if dep not in color:
                continue
            if color[dep] == _GRAY:
                cycle = [dep, node]
                cur = node
                p = parent[cur]
                while p is not None and p != dep:
                    cycle.append(p)
                    cur = p
                    p = parent[cur]
                cycle.reverse()
                return cycle
            if color[dep] == _WHITE:
                parent[dep] = node
                result = dfs(dep)
                if result:
                    return result
        color[node] = _BLACK
        return None

    for node in dep_map:
        if color[node] == _WHITE:
            result = dfs(node)
            if result:
                return result
    return None


def validate_specs(
    smd_files: list[Path], amd_files: list[Path]
) -> tuple[list[SMDSpec], list[AMDSpec]]:
    parsed_smds = [parse_smd_file(f) for f in smd_files]
    parsed_amds = [parse_amd_file(f) for f in amd_files]

    smd_spec_ids = [smd.spec_id for smd in parsed_smds]

    for smd in parsed_smds:
        for dep in smd.depends:
            if dep not in smd_spec_ids:
                raise ValidationError(
                    f"Spec {smd.spec_id} has dependency {dep} that doesn't exist."
                )

    dep_map = {smd.spec_id: list(smd.depends) for smd in parsed_smds}
    cycle = _detect_cycle(dep_map)
    if cycle:
        cycle_str = " -> ".join([*cycle, cycle[0]])
        raise ValidationError(f"Circular dependency detected: {cycle_str}")

    for amd in parsed_amds:
        if amd.spec_id not in smd_spec_ids:
            raise ValidationError(f"Architecture for spec {amd.spec_id} that doesn't exist.")

    return parsed_smds, parsed_amds


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


def _requirement_complexity(smd: SMDSpec) -> int:
    return sum(
        len(r.description)
        + len(r.accepts)
        + len(r.returns)
        + sum(len(c) + len(m) for c, m in r.errors)
        for r in smd.requirements
    )


def _warn_complex_specs(console: Console, parsed_smds: list[SMDSpec]) -> None:
    for smd in parsed_smds:
        complexity = _requirement_complexity(smd)
        if complexity > MAX_REQUIREMENT_COMPLEXITY:
            console.print(
                f"\n[yellow]WARNING:[/] {smd.spec_id} has high requirement complexity. "
                f"Complex specs may fail during planning.\n"
                f"Consider splitting into multiple specs linked with @depends."
            )


def run_validate(
    config_path: Path,
    verbose: bool,
    console: Console,
) -> None:
    from ossature.parsers.amd import AMDParseError
    from ossature.parsers.smd import SMDParseError

    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))

    if not smd_files:
        console.print("[yellow]No spec files found.[/]")
        return

    if not verbose:
        try:
            parsed_smds, parsed_amds = validate_specs(smd_files, amd_files)
        except (SMDParseError, AMDParseError, ValidationError) as e:
            console.print(f"[red]Validation Error:[/] {e}")
            raise SystemExit(1) from None

        console.print()
        console.print("[green]✓[/green] All checks passed")
        print_validation_summary(console, parsed_smds=parsed_smds, parsed_amds=parsed_amds)
        _warn_complex_specs(console, parsed_smds)
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

    # Cross-reference and cycle checks (reuse validate_specs logic on already-parsed specs)
    console.print()
    console.print("Cross-reference checks: ", end="")
    try:
        validate_specs(smd_files, amd_files)
        console.print("[green]✓ all checks passed")
    except ValidationError as e:
        console.print("[red]x")
        console.print(f" {e}")
        raise SystemExit(1) from None

    print_validation_summary(console, parsed_smds=parsed_smds, parsed_amds=parsed_amds)
    _warn_complex_specs(console, parsed_smds)
