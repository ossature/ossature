from enum import Enum
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

from ossature.models.amd import (
    AMDSpec,
    Component,
    DataModel,
    Dependency,
)
from ossature.models.shared import Status
from ossature.parsers.smd import parse_smd_file


def enum_choices(enum_class: type[Enum]) -> list[questionary.Choice]:
    return [questionary.Choice(title=e.value, value=e) for e in enum_class]


def ask_or_cancel(result: Any) -> Any:
    if result is None:
        raise KeyboardInterrupt
    return result


def find_smd_files(spec_dir: Path) -> list[Path]:
    return sorted(spec_dir.glob("*.smd"))


def extract_spec_id_from_smd(path: Path) -> str | None:
    try:
        return parse_smd_file(path).spec_id or None
    except Exception:
        return None


def get_available_specs(spec_dir: Path) -> list[tuple[str, str]]:
    specs = []
    for path in find_smd_files(spec_dir):
        spec_id = extract_spec_id_from_smd(path)
        if spec_id:
            specs.append((spec_id, path.name))
    return specs


def ask_spec_id(spec_dir: Path, console: Console) -> str | None:
    available_specs = get_available_specs(spec_dir)

    if not available_specs:
        console.print(
            Panel(
                "[red]No specification files found.[/red]\n\n"
                "An architecture document must be associated with a specification.\n"
                "Create a specification first with [cyan]ossature new <name>[/cyan]",
                title="Error",
                border_style="red",
            )
        )
        return None

    console.print("\n[bold underline]Select Specification[/bold underline]\n")

    spec_choices = [
        questionary.Choice(title=f"{spec_id} ({filename})", value=spec_id)
        for spec_id, filename in available_specs
    ]

    return str(
        ask_or_cancel(
            questionary.select(
                "Associate with specification:",
                choices=spec_choices,
            ).ask()
        )
    )


def prompt_component(console: Console, index: int) -> Component:
    console.print(Panel(f"[bold]Component {index}[/bold]", border_style="blue"))

    name = ask_or_cancel(questionary.text("Component name:").ask())
    path = ask_or_cancel(questionary.text("File path (e.g., src/services/auth.py):").ask())
    description = ask_or_cancel(questionary.text("Description:").ask())

    interface_language = ask_or_cancel(
        questionary.text(
            "Interface language (e.g., python, typescript, go):", default="python"
        ).ask()
    )

    console.print("[dim]Enter interface definition (empty line to finish):[/dim]")
    interface_lines = []
    while True:
        line = ask_or_cancel(questionary.text("").ask())
        if not line:
            break
        interface_lines.append(line)
    interface = "\n".join(interface_lines)

    console.print(
        "[dim]Contracts: behavioral guarantees the implementation must uphold,"
        " one per line (empty line to finish, leave empty for none):[/dim]"
    )
    contracts = []
    while True:
        line = ask_or_cancel(questionary.text("").ask())
        if not line.strip():
            break
        contracts.append(line.strip())

    depends_str = ask_or_cancel(
        questionary.text(
            "Depends on (comma-separated component names, or leave empty):", default=""
        ).ask()
    )
    depends_on = [d.strip() for d in depends_str.split(",") if d.strip()]

    return Component(
        name=name,
        path=path,
        description=description,
        interface=interface,
        interface_language=interface_language,
        contracts=contracts,
        depends_on=depends_on,
    )


def prompt_components(console: Console) -> list[Component]:
    components: list[Component] = []
    if not ask_or_cancel(questionary.confirm("Add components?", default=True).ask()):
        return components
    while True:
        component = prompt_component(console, len(components) + 1)
        components.append(component)
        if not ask_or_cancel(questionary.confirm("Add another component?", default=False).ask()):
            break
    return components


def prompt_data_model(console: Console, index: int) -> DataModel:
    console.print(Panel(f"[bold]Data Model {index}[/bold]", border_style="cyan"))

    name = ask_or_cancel(questionary.text("Model name:").ask())

    definition_language = ask_or_cancel(
        questionary.text(
            "Definition language (e.g., python, typescript, go):", default="python"
        ).ask()
    )

    console.print("[dim]Enter model definition (empty line to finish):[/dim]")
    definition_lines = []
    while True:
        line = ask_or_cancel(questionary.text("").ask())
        if not line:
            break
        definition_lines.append(line)
    definition = "\n".join(definition_lines)

    return DataModel(
        name=name,
        definition=definition,
        definition_language=definition_language,
    )


def prompt_data_models(console: Console) -> list[DataModel]:
    models: list[DataModel] = []
    if not ask_or_cancel(questionary.confirm("Add data models?", default=False).ask()):
        return models
    while True:
        model = prompt_data_model(console, len(models) + 1)
        models.append(model)
        if not ask_or_cancel(questionary.confirm("Add another data model?", default=False).ask()):
            break
    return models


def prompt_dependency(console: Console, index: int) -> Dependency:
    console.print(f"[dim]Dependency {index}[/dim]")
    name = ask_or_cancel(questionary.text("  Package/library name:").ask())
    purpose = ask_or_cancel(questionary.text("  Purpose:").ask())
    return Dependency(name=name, purpose=purpose)


def prompt_dependencies(console: Console) -> list[Dependency]:
    dependencies: list[Dependency] = []
    if not ask_or_cancel(questionary.confirm("Add external dependencies?", default=False).ask()):
        return dependencies
    while True:
        dep = prompt_dependency(console, len(dependencies) + 1)
        dependencies.append(dep)
        if not ask_or_cancel(questionary.confirm("Add another dependency?", default=False).ask()):
            break
    return dependencies


def prompt_flow(console: Console) -> str:
    if not ask_or_cancel(questionary.confirm("Add flow diagram?", default=False).ask()):
        return ""
    console.print("[dim]Enter flow diagram (empty line to finish):[/dim]")
    lines = []
    while True:
        line = ask_or_cancel(questionary.text("").ask())
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


def prompt_amd_spec(name: str, spec_dir: Path, console: Console) -> AMDSpec | None:
    try:
        console.print(
            Panel(
                "[bold]Ossature Architecture Wizard[/bold]\n\n"
                "Create a new architecture document interactively.\n"
                "Press [cyan]Ctrl+C[/cyan] at any time to cancel.",
                border_style="green",
            )
        )

        spec_id = ask_spec_id(spec_dir, console=console)
        if spec_id is None:
            return None

        console.print("\n[bold underline]Metadata[/bold underline]\n")

        default_title = name.replace("-", " ").replace("_", " ").title()
        title = ask_or_cancel(questionary.text("Title:", default=default_title).ask())

        status = ask_or_cancel(
            questionary.select(
                "Status:",
                choices=enum_choices(Status),
            ).ask()
        )

        console.print("\n[bold underline]Overview[/bold underline]\n")

        overview = ask_or_cancel(
            questionary.text("Overview (high-level architectural approach):", multiline=True).ask()
        )

        console.print("\n[bold underline]Components[/bold underline]")
        components = prompt_components(console)

        console.print("\n[bold underline]Data Models[/bold underline]")
        data_models = prompt_data_models(console)

        console.print("\n[bold underline]Flow[/bold underline]")
        flow = prompt_flow(console)

        console.print("\n[bold underline]Dependencies[/bold underline]")
        dependencies = prompt_dependencies(console)

        console.print("\n[bold underline]Notes[/bold underline]\n")
        notes = ask_or_cancel(
            questionary.text("Additional notes (optional):", default="", multiline=True).ask()
        )

        arch = AMDSpec(
            title=title,
            spec_id=spec_id,
            status=status,
            overview=overview,
            components=components,
            data_models=data_models,
            flow=flow,
            dependencies=dependencies,
            notes=notes,
        )

        console.print(
            Panel(
                f"[green]✓[/green] Architecture [cyan]{title}[/cyan] created with:\n\n"
                f"  • Linked to spec: {spec_id}\n"
                f"  • {len(components)} component(s)\n"
                f"  • {len(data_models)} data model(s)\n"
                f"  • {len(dependencies)} dependency(ies)",
                title="Summary",
                border_style="green",
            )
        )

        return arch

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return None
