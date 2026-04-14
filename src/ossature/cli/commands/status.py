import json
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ossature.config.loader import ConfigError, load_config
from ossature.models.plan import TaskStatus


def run_status(
    config_path: Path | None,
    console: Console,
    output_format: str = "text",
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        if output_format == "json":
            print(json.dumps({"error": str(e)}))
            raise SystemExit(1) from None
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    plan_filepath = config.metadata_path / "plan.toml"

    from ossature.audit.planner import load_plan

    plan = load_plan(plan_filepath)
    if not plan:
        if output_format == "json":
            print(json.dumps({"error": "No build plan found. Run ossature audit first."}))
        else:
            console.print("[yellow]No build plan found.[/] Run [cyan]ossature audit[/] first.")
        return

    # Per-spec stats
    spec_stats: dict[str, dict[str, int]] = {}
    for spec_id in plan.meta.specs:
        spec_stats[spec_id] = {"tasks": 0, "done": 0, "failed": 0, "pending": 0}

    for task in plan.tasks:
        if task.spec not in spec_stats:
            spec_stats[task.spec] = {"tasks": 0, "done": 0, "failed": 0, "pending": 0}
        spec_stats[task.spec]["tasks"] += 1
        if task.status == TaskStatus.DONE:
            spec_stats[task.spec]["done"] += 1
        elif task.status == TaskStatus.FAILED:
            spec_stats[task.spec]["failed"] += 1
        elif task.status == TaskStatus.PENDING:
            spec_stats[task.spec]["pending"] += 1

    failed_task = next((t for t in plan.tasks if t.status == TaskStatus.FAILED), None)

    if output_format == "json":
        specs_data = []
        for spec_id in plan.meta.specs:
            stats = spec_stats.get(spec_id, {"tasks": 0, "done": 0, "failed": 0, "pending": 0})
            if stats["failed"] > 0:
                status_str = "failed"
            elif stats["done"] == stats["tasks"] and stats["tasks"] > 0:
                status_str = "done"
            else:
                status_str = "pending"
            specs_data.append({"spec_id": spec_id, **stats, "status": status_str})

        result = {
            "name": config.name,
            "total_specs": len(plan.meta.specs),
            "total_tasks": plan.meta.total_tasks,
            "specs": specs_data,
            "failed_task": (
                {
                    "id": failed_task.id,
                    "spec": failed_task.spec,
                    "title": failed_task.title,
                }
                if failed_task
                else None
            ),
        }
        print(json.dumps(result))
        return

    console.print(
        f"[bold]{config.name}[/bold] — "
        f"{len(plan.meta.specs)} specs, {plan.meta.total_tasks} tasks\n"
    )

    tbl = Table(show_header=True, expand=False, pad_edge=False)
    tbl.add_column("Spec", style="bold cyan", no_wrap=True)
    tbl.add_column("Tasks", justify="right")
    tbl.add_column("Done", justify="right", style="green")
    tbl.add_column("Failed", justify="right", style="red")
    tbl.add_column("Pending", justify="right", style="dim")
    tbl.add_column("", no_wrap=True)

    for spec_id in plan.meta.specs:
        stats = spec_stats.get(spec_id, {"tasks": 0, "done": 0, "failed": 0, "pending": 0})

        if stats["failed"] > 0:
            indicator = Text("⟳", style="yellow")
        elif stats["done"] == stats["tasks"] and stats["tasks"] > 0:
            indicator = Text("✓", style="green")
        else:
            indicator = Text("·", style="dim")

        tbl.add_row(
            spec_id,
            str(stats["tasks"]),
            str(stats["done"]),
            str(stats["failed"]),
            str(stats["pending"]),
            indicator,
        )

    console.print(tbl)

    # Show current failing task if any
    if failed_task:
        console.print(
            f"\n  Current: {failed_task.spec} task {failed_task.id} "
            f"({failed_task.title}) — [red]FAILED[/red]"
        )
        console.print(
            "  Run [cyan]ossature retry[/] to re-attempt, "
            f"or [cyan]ossature build --spec {failed_task.spec}[/] for targeted rebuild"
        )
