from pathlib import Path

import click
from rich.console import Console

from ossature import __app_name__, __version__

console = Console()


class NaturalOrderGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


@click.group(cls=NaturalOrderGroup)
@click.version_option(version=__version__, prog_name=__app_name__)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to ossature.toml config file",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    """Ossature - Specification and architecture driven code generation toolkit."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose
    ctx.obj["console"] = console


@cli.command()
@click.argument("name", default=".")
@click.pass_context
def init(ctx: click.Context, name: str) -> None:
    """Initialize a new Ossature project."""
    from ossature.cli.commands.init import run_init

    run_init(
        name=name,
        console=ctx.obj["console"],
    )


@cli.command()
@click.argument("name")
@click.option(
    "--type",
    "-t",
    "spec_type",
    type=click.Choice(["smd", "amd"]),
    default="smd",
    help="Type of spec to create (defaults to smd)",
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Create spec interactively",
)
@click.pass_context
def new(
    ctx: click.Context,
    name: str,
    spec_type: str,
    interactive: bool,
) -> None:
    """Create a new spec file."""
    from ossature.cli.commands.new import run_new

    run_new(
        name=name,
        spec_type=spec_type,
        interactive=interactive,
        config_path=ctx.obj["config_path"],
        console=ctx.obj["console"],
    )


@cli.command()
@click.pass_context
def validate(
    ctx: click.Context,
) -> None:
    """Validate config and spec files."""
    from ossature.cli.commands.validate import run_validate

    run_validate(
        config_path=ctx.obj["config_path"],
        verbose=ctx.obj["verbose"],
        console=ctx.obj["console"],
    )


@cli.command()
@click.option(
    "--replan",
    is_flag=True,
    help="Regenerate the build plan (discards manual edits to plan.toml)",
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Prompt before each auto-fix (default runs non-interactively)",
)
@click.option(
    "--no-fix",
    is_flag=True,
    help="Audit only, never attempt auto-fix",
)
@click.option(
    "--errors-ok",
    is_flag=True,
    help="Exit 0 even when audit errors remain",
)
@click.pass_context
def audit(
    ctx: click.Context,
    replan: bool,
    interactive: bool,
    no_fix: bool,
    errors_ok: bool,
) -> None:
    """Semantically audit the specifications and generate build plan metadata."""
    from ossature.cli.commands.audit import run_audit

    if interactive and no_fix:
        ctx.obj["console"].print(
            "[red]Error:[/] --interactive and --no-fix are mutually exclusive."
        )
        raise SystemExit(1)

    run_audit(
        config_path=ctx.obj["config_path"],
        verbose=ctx.obj["verbose"],
        console=ctx.obj["console"],
        replan=replan,
        interactive=interactive,
        no_fix=no_fix,
        errors_ok=errors_ok,
    )


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show current build progress, completed/pending/failed tasks."""
    from ossature.cli.commands.status import run_status

    run_status(
        config_path=ctx.obj["config_path"],
        console=ctx.obj["console"],
    )


@cli.command()
@click.option(
    "--from",
    "from_task",
    type=str,
    default=None,
    help="Re-run from this task ID onwards",
)
@click.option(
    "--only",
    type=str,
    default=None,
    help="Re-run just this task (and re-verify dependents)",
)
@click.pass_context
def retry(
    ctx: click.Context,
    from_task: str | None,
    only: str | None,
) -> None:
    """Re-run failed or invalidated tasks from last build."""
    from ossature.cli.commands.retry import run_retry

    if from_task and only:
        ctx.obj["console"].print("[red]Error:[/] --from and --only are mutually exclusive.")
        raise SystemExit(1)

    run_retry(
        config_path=ctx.obj["config_path"],
        verbose=ctx.obj["verbose"],
        console=ctx.obj["console"],
        from_task=from_task,
        only_task=only,
    )


@cli.command()
@click.pass_context
def clean(ctx: click.Context) -> None:
    """Remove .ossature/ state directory (full reset)."""
    from ossature.cli.commands.clean import run_clean

    run_clean(
        config_path=ctx.obj["config_path"],
        console=ctx.obj["console"],
    )


@cli.command()
@click.option(
    "--step",
    is_flag=True,
    help="Pause after every task for approval",
)
@click.option(
    "--auto",
    is_flag=True,
    help="Run to completion, only stop on failure",
)
@click.option(
    "--skip-failures",
    is_flag=True,
    help="Skip failed tasks and continue (requires --auto)",
)
@click.option(
    "--spec",
    "spec_filter",
    type=str,
    default=None,
    help="Build only this spec (and its dependencies if needed)",
)
@click.option(
    "--force",
    is_flag=True,
    help="Full rebuild, ignore all cached state",
)
@click.pass_context
def build(
    ctx: click.Context,
    step: bool,
    auto: bool,
    skip_failures: bool,
    spec_filter: str | None,
    force: bool,
) -> None:
    """Execute the build plan, generating code task-by-task with LLM."""
    from ossature.cli.commands.build import run_build

    if step and auto:
        ctx.obj["console"].print("[red]Error:[/] --step and --auto are mutually exclusive.")
        raise SystemExit(1)

    if skip_failures and not auto:
        ctx.obj["console"].print("[red]Error:[/] --skip-failures requires --auto.")
        raise SystemExit(1)

    run_build(
        config_path=ctx.obj["config_path"],
        verbose=ctx.obj["verbose"],
        console=ctx.obj["console"],
        step=step,
        auto=auto,
        skip_failures=skip_failures,
        spec_filter=spec_filter,
        force=force,
    )
