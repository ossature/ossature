from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel

from ossature.cli.wizard.amd import ask_or_cancel, ask_spec_id, enum_choices
from ossature.models.shared import Status
from ossature.models.vmd import VMDSpec
from ossature.parsers.vmd import VMDParseError, parse_vmd


def prompt_value_group(console: Console, index: int) -> list[str]:
    """Collect one function group as raw VMD lines."""
    console.print(Panel(f"[bold]Group {index}[/bold]", border_style="blue"))

    target = ask_or_cancel(questionary.text("Function under test:").ask())
    params_str = ask_or_cancel(
        questionary.text("Parameter names (comma-separated, or leave empty):", default="").ask()
    )
    params = [p.strip() for p in params_str.split(",") if p.strip()]
    lines = [f"{target}({', '.join(params)})"]

    case_index = 1
    while True:
        console.print(f"[dim]Case {case_index}[/dim]")
        name = ask_or_cancel(questionary.text("  Case name:", default=f"case_{case_index}").ask())
        cells = [name]
        for param in params:
            cells.append(
                ask_or_cancel(
                    questionary.text(f'  Input for {param} (JSON, e.g. "text" or 42):').ask()
                )
            )
        cells.append(
            ask_or_cancel(
                questionary.text("  Expected (JSON value, !ErrorType: message, or Ok):").ask()
            )
        )
        lines.append(" | ".join(cells))
        case_index += 1
        if not ask_or_cancel(questionary.confirm("Add another case?", default=False).ask()):
            break
    return lines


def prompt_cli_group(console: Console, index: int) -> list[str]:
    """Collect one command group as raw VMD lines."""
    console.print(Panel(f"[bold]Group {index}[/bold]", border_style="blue"))

    target = ask_or_cancel(questionary.text("Command under test:").ask())
    lines = [f"{target}(argv) ~cli"]

    case_index = 1
    while True:
        console.print(f"[dim]Case {case_index}[/dim]")
        name = ask_or_cancel(questionary.text("  Case name:", default=f"case_{case_index}").ask())
        argv = ask_or_cancel(
            questionary.text('  argv (JSON array, e.g. ["--flag", "value"]):').ask()
        )
        stdout = ask_or_cancel(
            questionary.text(
                "  Expected stdout (JSON string, or leave empty to skip):", default=""
            ).ask()
        )
        exit_code = ask_or_cancel(
            questionary.text("  Expected exit code (or leave empty to skip):", default="").ask()
        )
        stderr = ask_or_cancel(
            questionary.text(
                "  Expected stderr (JSON string, or leave empty to skip):", default=""
            ).ask()
        )
        cells = [name, argv, stdout.strip(), exit_code.strip(), stderr.strip()]
        while len(cells) > 2 and not cells[-1]:
            cells.pop()
        lines.append(" | ".join(cells))
        case_index += 1
        if not ask_or_cancel(questionary.confirm("Add another case?", default=False).ask()):
            break
    return lines


def prompt_vmd_spec(name: str, spec_dir: Path, console: Console) -> VMDSpec | None:
    try:
        console.print(
            Panel(
                "[bold]Ossature Verification Wizard[/bold]\n\n"
                "Create a new verification file interactively.\n"
                "Press [cyan]Ctrl+C[/cyan] at any time to cancel.",
                border_style="green",
            )
        )

        spec_id = ask_spec_id(spec_dir, console=console, document_label="A verification file")
        if spec_id is None:
            return None

        console.print("\n[bold underline]Metadata[/bold underline]\n")

        status = ask_or_cancel(
            questionary.select(
                "Status:",
                choices=enum_choices(Status),
            ).ask()
        )

        console.print("\n[bold underline]Groups[/bold underline]")

        text_lines = [f"@spec {spec_id}", f"@status {status.value}"]
        group_index = 1
        while True:
            kind = ask_or_cancel(
                questionary.select(
                    "Group type:",
                    choices=[
                        questionary.Choice(title="function (call with inputs)", value="value"),
                        questionary.Choice(title="command (run a program)", value="cli"),
                    ],
                ).ask()
            )
            if kind == "cli":
                block = prompt_cli_group(console, group_index)
            else:
                block = prompt_value_group(console, group_index)
            text_lines.append("")
            text_lines.extend(block)
            group_index += 1
            if not ask_or_cancel(questionary.confirm("Add another group?", default=False).ask()):
                break

        text = "\n".join(text_lines) + "\n"
        try:
            spec = parse_vmd(text)
        except VMDParseError as e:
            console.print(
                Panel(
                    f"[red]The entered cases do not parse:[/red]\n\n{e}",
                    title="Error",
                    border_style="red",
                )
            )
            return None

        case_count = sum(len(g.case_names) for g in spec.groups)
        console.print(
            Panel(
                f"[green]✓[/green] Verification for [cyan]{spec_id}[/cyan] created with:\n\n"
                f"  • {len(spec.groups)} group(s)\n"
                f"  • {case_count} case(s)",
                title="Summary",
                border_style="green",
            )
        )

        return spec

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return None
