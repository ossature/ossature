import shutil
from pathlib import Path

import questionary
from rich.console import Console

from ossature.cli.decorators import load_config_or_exit


def run_clean(
    config_path: Path | None,
    console: Console,
) -> None:
    config = load_config_or_exit(config_path, console)

    ntt_dir = config.metadata_path

    if not ntt_dir.exists():
        console.print("[yellow]Nothing to clean.[/] No .ossature/ directory found.")
        return

    if not questionary.confirm(
        "This will delete any previously generated audits, plans, or state files. "
        "Are you sure you want to continue?",
        default=False,
    ).ask():
        raise SystemExit(0)

    shutil.rmtree(ntt_dir)
    console.print("[green]✓[/] Removed .ossature/ - full reset complete.")
