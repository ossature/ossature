import json
from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel

from ossature.cli.wizard.amd import ask_or_cancel, ask_spec_id, enum_choices
from ossature.models.shared import Status
from ossature.models.vmd import VMDSpec
from ossature.parsers.vmd import VMDParseError, parse_vmd


def prompt_value_group(console: Console, index: int) -> list[str]:
    """Collect one table group as raw VMD lines."""
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


def prompt_command_scenario(console: Console, index: int) -> list[str]:
    """Collect one command scenario as raw VMD lines."""
    console.print(Panel(f"[bold]Scenario {index}[/bold]", border_style="blue"))

    name = ask_or_cancel(questionary.text("Scenario name (a short behavior description):").ask())
    lines = [f"scenario {name}:"]

    step_index = 1
    while True:
        console.print(f"[dim]Step {step_index}[/dim]")
        command = ask_or_cancel(questionary.text("  Command (e.g. my-tool --flag value):").ask())
        lines.append(f"when $ {command}")
        exit_code = ask_or_cancel(
            questionary.text("  Expected exit code:", default="0").ask()
        ).strip()
        if exit_code and exit_code != "0":
            lines.append(f"then exit {exit_code}")
        stdout = ask_or_cancel(
            questionary.text("  stdout contains (or leave empty to skip):", default="").ask()
        ).strip()
        if stdout:
            lines.append(f"then stdout has {json.dumps(stdout)}")
        stderr = ask_or_cancel(
            questionary.text("  stderr contains (or leave empty to skip):", default="").ask()
        ).strip()
        if stderr:
            lines.append(f"then stderr has {json.dumps(stderr)}")
        step_index += 1
        if not ask_or_cancel(questionary.confirm("Add another step?", default=False).ask()):
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

        console.print("\n[bold underline]Cases[/bold underline]")

        text_lines = [f"@spec {spec_id}", f"@status {status.value}"]
        entry_index = 1
        while True:
            kind = ask_or_cancel(
                questionary.select(
                    "What are you testing?",
                    choices=[
                        questionary.Choice(
                            title="a function (table of inputs and expected values)",
                            value="value",
                        ),
                        questionary.Choice(
                            title="a command (scenario of runs and expectations)",
                            value="command",
                        ),
                    ],
                ).ask()
            )
            if kind == "command":
                block = prompt_command_scenario(console, entry_index)
            else:
                block = prompt_value_group(console, entry_index)
            text_lines.append("")
            text_lines.extend(block)
            entry_index += 1
            if not ask_or_cancel(
                questionary.confirm("Add another group or scenario?", default=False).ask()
            ):
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

        case_count = sum(len(g.cases) for g in spec.groups) + len(spec.scenarios)
        console.print(
            Panel(
                f"[green]✓[/green] Verification for [cyan]{spec_id}[/cyan] created with:\n\n"
                f"  • {len(spec.groups)} group(s)\n"
                f"  • {len(spec.scenarios)} scenario(s)\n"
                f"  • {case_count} case(s)",
                title="Summary",
                border_style="green",
            )
        )

        return spec

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return None
