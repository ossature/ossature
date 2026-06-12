from pathlib import Path

from rich.console import Console

from ossature.cli.decorators import requires_llm
from ossature.config.loader import ConfigError, load_config
from ossature.models.amd import AMDSpec
from ossature.models.plan import TaskStatus
from ossature.parsers.amd import AMDParseError, parse_amd_file
from ossature.parsers.smd import SMDParseError, parse_smd_file


def _resolve_spec_filter(
    spec_filter: str,
    plan_specs: list[str],
    smd_deps: dict[str, list[str]],
    console: Console,
) -> set[str]:
    target = spec_filter.upper()
    if target not in plan_specs:
        console.print(
            f"[red]Error:[/] Unknown spec '{spec_filter}'. Available: {', '.join(plan_specs)}"
        )
        raise SystemExit(1)

    # Collect transitive dependencies
    needed: set[str] = set()
    queue = [target]
    while queue:
        sid = queue.pop()
        if sid in needed:
            continue
        needed.add(sid)
        for dep in smd_deps.get(sid, []):
            if dep not in needed:
                queue.append(dep)

    return needed


@requires_llm
def run_build(
    config_path: Path | None,
    verbose: bool,
    console: Console,
    step: bool = False,
    auto: bool = False,
    skip_failures: bool = False,
    spec_filter: str | None = None,
    force: bool = False,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    plan_filepath = config.metadata_path / "plan.toml"

    from ossature.audit.planner import load_plan

    plan = load_plan(plan_filepath)
    if not plan:
        console.print("[red]No plan found. Run `ossature audit` first.[/red]")
        raise SystemExit(1)

    # --force: reset all task statuses to pending
    if force:
        for task in plan.tasks:
            task.status = TaskStatus.PENDING

    # --spec: filter to target spec + transitive dependencies
    if spec_filter:
        smd_files = list(config.spec_path.glob("**/*.smd"))
        parsed_smds_for_deps = [parse_smd_file(f) for f in smd_files]
        smd_deps = {smd.spec_id: list(smd.depends) for smd in parsed_smds_for_deps}

        needed_specs = _resolve_spec_filter(spec_filter, plan.meta.specs, smd_deps, console)

        # Mark tasks outside needed specs as skipped (if not already done)
        for task in plan.tasks:
            if task.spec not in needed_specs and task.status != TaskStatus.DONE:
                task.status = TaskStatus.SKIPPED

    pending = sum(1 for t in plan.tasks if t.status.value == "pending")
    failed = sum(1 for t in plan.tasks if t.status.value == "failed")
    actionable = pending + failed
    if actionable == 0:
        console.print("[green]All tasks already completed.[/green]")
        return

    status_parts = [f"{plan.meta.total_tasks} tasks"]
    if pending:
        status_parts.append(f"{pending} pending")
    if failed:
        status_parts.append(f"{failed} failed")
    if spec_filter:
        status_parts.append(f"spec: {spec_filter.upper()}")

    console.print(f"[bold]{config.name} v{config.version}[/bold] — {', '.join(status_parts)}\n")

    # Parse specs for context assembly
    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))

    try:
        parsed_smds = [parse_smd_file(f) for f in smd_files]
        parsed_amds = [parse_amd_file(f) for f in amd_files]
    except SMDParseError, AMDParseError:
        console.print("[red]Specs invalid. Run `ossature validate` to see errors.")
        raise SystemExit(1) from None

    smd_map = {smd.spec_id: smd for smd in parsed_smds}
    amd_by_spec: dict[str, list[AMDSpec]] = {}
    for amd in parsed_amds:
        amd_by_spec.setdefault(amd.spec_id, []).append(amd)

    from ossature.build.builder import BuildMode, execute_build

    if step:
        mode = BuildMode.STEP
    elif auto and skip_failures:
        mode = BuildMode.AUTO_SKIP
    elif auto:
        mode = BuildMode.AUTO
    else:
        mode = BuildMode.DEFAULT

    execute_build(config, plan, smd_map, amd_by_spec, console, plan_filepath, mode, verbose)
