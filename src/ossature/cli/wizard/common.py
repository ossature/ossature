"""Shared wizard helpers used by the SMD, AMD, and VMD interactive builders."""

from enum import Enum
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

from ossature.parsers.smd import parse_smd_file


def enum_choices(enum_class: type[Enum]) -> list[questionary.Choice]:
    return [questionary.Choice(title=e.value, value=e) for e in enum_class]


def ask_or_cancel(result: Any) -> Any:
    if result is None:
        raise KeyboardInterrupt
    return result


def prompt_list(prompt_text: str, console: Console) -> list[str]:
    items = []
    console.print("[dim]Enter items one at a time. Empty line to finish.[/dim]")
    while True:
        item = ask_or_cancel(questionary.text(f"{prompt_text}:").ask())
        if not item.strip():
            break
        items.append(item.strip())
    return items


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


def ask_spec_id(
    spec_dir: Path,
    console: Console,
    document_label: str = "An architecture document",
) -> str | None:
    available_specs = get_available_specs(spec_dir)

    if not available_specs:
        console.print(
            Panel(
                "[red]No specification files found.[/red]\n\n"
                f"{document_label} must be associated with a specification.\n"
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
