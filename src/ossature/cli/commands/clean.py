import shutil
from pathlib import Path

import questionary
from rich.console import Console

from ossature.config.loader import ConfigError, load_config


def run_clean(
    config_path: Path | None,
    console: Console,
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        from rich.markup import escape

        console.print(f"[red]Error:[/] {escape(str(e))}")
        raise SystemExit(1) from None

    ntt_dir = config.metadata_path

    if not ntt_dir.exists():
        console.print("[yellow]Nothing to clean.[/] No .ossature/ directory found.")
        return

    if dry_run:
        console.print(f"[dim]Would remove:[/] {ntt_dir}")
        return

    if (
        not yes
        and not questionary.confirm(
            "This will delete any previously generated audits, plans, or state files. "
            "Are you sure you want to continue?",
            default=False,
        ).ask()
    ):
        raise SystemExit(0)

    shutil.rmtree(ntt_dir)
    console.print("[green]✓[/] Removed .ossature/ — full reset complete.")
