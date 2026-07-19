from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from ossature.templates.manager import TemplateManager


def run_init(
    name: str,
    console: Console,
) -> None:
    if name == ".":
        root = Path.cwd()
        project_name = root.name
    else:
        root = Path.cwd() / name
        project_name = name
        root.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold]Initializing Ossature project:[/] {project_name}\n")

    manager = TemplateManager(root)
    result = manager.init_project(
        name=project_name,
    )

    if result.created:
        console.print("[green]Created:[/]")
        for path in result.created:
            rel_path = path.relative_to(root) if path.is_relative_to(root) else path
            console.print(f"  • {rel_path}")

    if result.skipped:
        console.print("\n[yellow]Skipped (already exists):[/]")
        for path in result.skipped:
            rel_path = path.relative_to(root) if path.is_relative_to(root) else path
            console.print(f"  • {rel_path}")

    if result.errors:
        console.print("\n[red]Errors:[/]")
        for error in result.errors:
            console.print(f"  • {error}")

    if result.success:
        console.print(
            Panel(
                "[green]✓[/] Project initialized successfully!",
                title="Success",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[red]✗[/] Project initialization had errors",
                title="Error",
                border_style="red",
            )
        )
        raise SystemExit(1)
