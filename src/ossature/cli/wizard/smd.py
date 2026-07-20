import questionary
from rich.console import Console
from rich.panel import Panel

from ossature.cli.wizard.common import ask_or_cancel, enum_choices, prompt_list
from ossature.models.shared import Status
from ossature.models.smd import (
    Example,
    Priority,
    Requirement,
    SMDSpec,
)


def prompt_error(console: Console, index: int) -> tuple[str, str]:
    console.print(f"[dim]Error {index}[/dim]")
    condition = ask_or_cancel(questionary.text("  Condition (e.g., 'Invalid credentials'):").ask())
    response = ask_or_cancel(questionary.text("  Response (e.g., '401 with error message'):").ask())
    return (condition, response)


def prompt_errors(console: Console) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    if not ask_or_cancel(questionary.confirm("Add error cases?", default=False).ask()):
        return errors
    while True:
        error = prompt_error(console, len(errors) + 1)
        errors.append(error)
        if not ask_or_cancel(questionary.confirm("Add another error?", default=False).ask()):
            break
    return errors


def prompt_requirement(console: Console, index: int) -> Requirement:
    console.print(Panel(f"[bold]Requirement {index}[/bold]", border_style="blue"))

    title = ask_or_cancel(questionary.text("Title:").ask())
    description = ask_or_cancel(questionary.text("Description:").ask())
    accepts = ask_or_cancel(questionary.text("Accepts (valid inputs):").ask())
    returns = ask_or_cancel(questionary.text("Returns (expected output):").ask())
    errors = prompt_errors(console)

    return Requirement(
        title=title,
        description=description,
        accepts=accepts,
        returns=returns,
        errors=errors,
    )


def prompt_requirements(console: Console) -> list[Requirement]:
    requirements: list[Requirement] = []
    console.print("\n[yellow]At least one requirement is needed.[/yellow]")
    while True:
        req = prompt_requirement(console, len(requirements) + 1)
        requirements.append(req)
        if not ask_or_cancel(questionary.confirm("Add another requirement?", default=False).ask()):
            break
    return requirements


def prompt_example(console: Console, index: int) -> Example:
    console.print(Panel(f"[bold]Example {index}[/bold]", border_style="cyan"))

    name = ask_or_cancel(questionary.text("Example name:").ask())
    console.print("[dim]Enter input (multi-line, empty line to finish):[/dim]")
    input_lines = []
    while True:
        line = ask_or_cancel(questionary.text("").ask())
        if not line:
            break
        input_lines.append(line)
    input_text = "\n".join(input_lines)

    console.print("[dim]Enter expected output (multi-line, empty line to finish):[/dim]")
    output_lines = []
    while True:
        line = ask_or_cancel(questionary.text("").ask())
        if not line:
            break
        output_lines.append(line)
    output_text = "\n".join(output_lines)

    return Example(name=name, input=input_text, output=output_text)


def prompt_examples(console: Console) -> list[Example]:
    examples: list[Example] = []
    if not ask_or_cancel(questionary.confirm("Add examples?", default=False).ask()):
        return examples
    while True:
        example = prompt_example(console, len(examples) + 1)
        examples.append(example)
        if not ask_or_cancel(questionary.confirm("Add another example?", default=False).ask()):
            break
    return examples


def prompt_smd_spec(name: str, console: Console) -> SMDSpec | None:
    try:
        console.print(
            Panel(
                "[bold]Ossature Spec Wizard[/bold]\n\n"
                "Create a new specification interactively.\n"
                "Press [cyan]Ctrl+C[/cyan] at any time to cancel.",
                border_style="green",
            )
        )

        # Metadata
        console.print("\n[bold underline]Metadata[/bold underline]\n")

        default_title = name.replace("-", " ").replace("_", " ").title()
        title = ask_or_cancel(questionary.text("Title:", default=default_title).ask())

        default_spec_id = name.upper().replace("-", "_").replace(" ", "_")
        spec_id = ask_or_cancel(questionary.text("Spec ID:", default=default_spec_id).ask())

        status = ask_or_cancel(
            questionary.select(
                "Status:",
                choices=enum_choices(Status),
            ).ask()
        )

        priority = ask_or_cancel(
            questionary.select(
                "Priority:",
                choices=enum_choices(Priority),
            ).ask()
        )

        depends_str = ask_or_cancel(
            questionary.text(
                "Dependencies (comma-separated spec IDs, or leave empty):", default=""
            ).ask()
        )
        depends = [d.strip() for d in depends_str.split(",") if d.strip()]

        # Overview
        console.print("\n[bold underline]Overview[/bold underline]\n")
        overview = ask_or_cancel(
            questionary.text("Overview (what does this feature do?):", multiline=True).ask()
        )

        # Goals
        console.print("\n[bold underline]Goals[/bold underline]")
        goals = prompt_list("Goal", console)

        # Non-Goals
        console.print("\n[bold underline]Non-Goals[/bold underline]")
        non_goals = prompt_list("Non-goal", console)

        # Requirements
        console.print("\n[bold underline]Requirements[/bold underline]")
        requirements = prompt_requirements(console)

        # Constraints
        console.print("\n[bold underline]Constraints[/bold underline]")
        constraints = prompt_list("Constraint", console)

        # Examples
        console.print("\n[bold underline]Examples[/bold underline]")
        examples = prompt_examples(console)

        # Acceptance Criteria
        console.print("\n[bold underline]Acceptance Criteria[/bold underline]")
        acceptance_criteria = prompt_list("Criterion", console)

        # Notes
        console.print("\n[bold underline]Notes[/bold underline]\n")
        notes = ask_or_cancel(questionary.text("Additional notes (optional):", default="").ask())

        spec = SMDSpec(
            title=title,
            spec_id=spec_id,
            status=status,
            priority=priority,
            overview=overview,
            depends=depends,
            goals=goals,
            non_goals=non_goals,
            requirements=requirements,
            constraints=constraints,
            examples=examples,
            acceptance_criteria=acceptance_criteria,
            notes=notes,
        )

        return spec

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return None
