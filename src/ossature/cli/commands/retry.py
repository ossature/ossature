from pathlib import Path

from rich.console import Console

from ossature.cli.decorators import requires_llm
from ossature.config.loader import ConfigError, load_config
from ossature.models.amd import AMDSpec
from ossature.models.plan import PlanTask, TaskStatus
from ossature.parsers.amd import parse_amd_file
from ossature.parsers.smd import parse_smd_file


def _collect_dependents(task_id: str, plan_tasks: list[PlanTask]) -> set[str]:
    """Find all tasks that transitively depend on the given task."""
    dependents: set[str] = set()
    queue = [task_id]
    while queue:
        current = queue.pop()
        for task in plan_tasks:
            if current in task.depends_on and task.id not in dependents:
                dependents.add(task.id)
                queue.append(task.id)
    return dependents


@requires_llm
def run_retry(
    config_path: Path | None,
    verbose: bool,
    console: Console,
    from_task: str | None = None,
    only_task: str | None = None,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    plan_filepath = config.metadata_path / "plan.toml"

    from ossature.audit.planner import load_plan, write_plan

    plan = load_plan(plan_filepath)
    if not plan:
        console.print("[red]No plan found. Run `ossature audit` first.[/red]")
        raise SystemExit(1)

    task_ids = {t.id for t in plan.tasks}

    if only_task:
        only_task = only_task.zfill(3)
        if only_task not in task_ids:
            console.print(
                f"[red]Error:[/] Unknown task '{only_task}'. "
                f"Valid range: {plan.tasks[0].id}-{plan.tasks[-1].id}"
            )
            raise SystemExit(1)

        to_reset = {only_task} | _collect_dependents(only_task, plan.tasks)
        reset_count = 0
        for task in plan.tasks:
            if task.id in to_reset and task.status != TaskStatus.PENDING:
                task.status = TaskStatus.PENDING
                reset_count += 1

        if reset_count == 0:
            console.print(f"[yellow]Task {only_task} is already pending.[/yellow]")
            return

        dep_count = reset_count - 1
        msg = f"Reset task {only_task} to pending"
        if dep_count > 0:
            msg += f" (+{dep_count} dependent{'s' if dep_count != 1 else ''})"
        console.print(f"[cyan]{msg}[/cyan]\n")

    elif from_task:
        from_task = from_task.zfill(3)
        if from_task not in task_ids:
            console.print(
                f"[red]Error:[/] Unknown task '{from_task}'. "
                f"Valid range: {plan.tasks[0].id}-{plan.tasks[-1].id}"
            )
            raise SystemExit(1)

        reset_count = 0
        for task in plan.tasks:
            if task.id >= from_task and task.status != TaskStatus.PENDING:
                task.status = TaskStatus.PENDING
                reset_count += 1

        if reset_count == 0:
            console.print(
                f"[yellow]All tasks from {from_task} onwards are already pending.[/yellow]"
            )
            return

        console.print(
            f"[cyan]Reset {reset_count} task{'s' if reset_count != 1 else ''}"
            f" from {from_task} onwards[/cyan]\n"
        )

    else:
        failed_count = 0
        for task in plan.tasks:
            if task.status == TaskStatus.FAILED:
                task.status = TaskStatus.PENDING
                failed_count += 1

        if failed_count == 0:
            console.print("[yellow]No failed tasks to retry.[/yellow]")
            return

        console.print(
            f"[cyan]Reset {failed_count} failed task{'s' if failed_count != 1 else ''}"
            f" to pending[/cyan]\n"
        )

    write_plan(plan, plan_filepath)

    # Parse specs for context assembly and delegate to build
    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))

    parsed_smds = [parse_smd_file(f) for f in smd_files]
    parsed_amds = [parse_amd_file(f) for f in amd_files]

    smd_map = {smd.spec_id: smd for smd in parsed_smds}
    amd_by_spec: dict[str, list[AMDSpec]] = {}
    for amd in parsed_amds:
        amd_by_spec.setdefault(amd.spec_id, []).append(amd)

    from ossature.build.builder import BuildMode, execute_build

    execute_build(
        config, plan, smd_map, amd_by_spec, console, plan_filepath, BuildMode.DEFAULT, verbose
    )
