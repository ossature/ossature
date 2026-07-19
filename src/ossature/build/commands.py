"""Shell command execution: setup, verify, and tool availability checks."""

import shlex
import shutil
import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from ossature.config.loader import OssatureConfig
from ossature.models.plan import Plan


def is_verify_command_error(error_output: str, output_dir: Path) -> bool:
    output_str = str(output_dir.resolve())
    lines = error_output.strip().splitlines()
    # Filter out hint/info lines to look at actual error content
    error_lines = [
        ln for ln in lines if not ln.strip().startswith(("Hint:", "hint:", "Info:", "info:"))
    ]

    if not error_lines:
        return False

    # If no error line references a file inside the output directory,
    # it's likely a command-level problem, not a source-code problem.
    has_source_ref = any(output_str in ln or ("Error:" in ln and "/" in ln) for ln in error_lines)

    # Common patterns for command invocation errors
    invocation_patterns = [
        "arguments can only be given if",
        "unknown option",
        "unrecognized option",
        "invalid option",
        "unknown command",
        "unrecognized command",
        "command not found",
        "no such subcommand",
        "usage:",
        "USAGE:",
        "unexpected argument",
        "invalid argument",
        "not a valid",
    ]
    error_text = error_output.lower()
    has_invocation_signal = any(pat.lower() in error_text for pat in invocation_patterns)

    return has_invocation_signal and not has_source_ref


def run_verify(commands: list[str], cwd: Path) -> tuple[bool, str]:
    """Run verify commands in order, fail-fast on first non-zero exit.

    Each command runs in a fresh shell. Output from successive commands
    is concatenated (with command headers) so failures in any step are
    self-describing.
    """
    if not commands:
        return True, ""

    combined: list[str] = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=str(cwd),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return False, "Verify command timed out after 120 seconds"

        step_output = ""
        if result.stdout:
            step_output += result.stdout
        if result.stderr:
            if step_output:
                step_output += "\n"
            step_output += result.stderr
        step_output = step_output.strip()

        if len(commands) > 1:
            header = f"$ {command}"
            combined.append(header if not step_output else f"{header}\n{step_output}")
        elif step_output:
            combined.append(step_output)

        if result.returncode != 0:
            return False, "\n".join(combined).strip()

    return True, "\n".join(combined).strip()


def _truncate_output(text: str, max_lines: int = 30) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = [*lines[:10], f"  ... ({len(lines) - 20} lines omitted) ...", *lines[-10:]]
    return "\n".join(kept)


def _format_verify_for_display(commands: list[str]) -> str:
    """Render a verify command list as a single string for status/error messages."""
    if not commands:
        return ""
    if len(commands) == 1:
        return commands[0]
    return " && ".join(commands)


def run_setup(config: OssatureConfig, console: Console) -> bool:
    if not config.build.setup:
        return True

    for command in config.build.setup:
        console.print(f"  Running setup: [bold]{command}[/bold]")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=str(config.output_path),
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            console.print("[red]Setup command timed out after 300 seconds.[/red]")
            return False

        if result.returncode != 0:
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                if output:
                    output += "\n"
                output += result.stderr
            console.print(f"[red]Setup command failed (exit {result.returncode}):[/red]")
            if output.strip():
                console.print(
                    Panel(
                        _truncate_output(output.strip()),
                        border_style="red",
                        expand=True,
                        padding=(0, 1),
                    )
                )
            return False

    console.print("  [green]Setup complete.[/green]")
    return True


# Shell operators that delimit sub-commands within a single shell string.
_SHELL_OPERATORS: frozenset[str] = frozenset({"&&", "||", ";", "|"})

# Shell builtins whose first-token presence does not require a binary on PATH.
_SHELL_BUILTINS: frozenset[str] = frozenset(
    {"cd", "echo", "export", "test", "[", "true", "false", ":", "exit", "set", "unset"}
)


def _command_groups_from_plan(plan: Plan, config: OssatureConfig) -> list[list[str]]:
    """Collect verify/setup/test command lists into per-scope groups.

    Each group is a list of shell-command strings that share a sequential
    execution context — outputs produced by an earlier item in the group
    are visible to later items, but not across groups.
    """
    groups: list[list[str]] = []
    if config.build.setup:
        groups.append(list(config.build.setup))
    if config.build.verify:
        groups.append(list(config.build.verify))
    for task in plan.tasks:
        if task.verify:
            groups.append(list(task.verify))
    return groups


def _split_tokens(command: str) -> list[str]:
    """Tokenize a shell command, falling back to whitespace split on bad quoting."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _extract_executables_for_group(group: list[str]) -> dict[str, str]:
    """Return a mapping of ``executable -> originating command`` for the group.

    The check we perform is intentionally narrow and language-agnostic:
    we flag only tokens the shell would actually resolve via ``PATH``.
    Per POSIX, ``PATH`` is consulted **only** when the command name
    contains no ``/``. Anything with a slash (``./yep``,
    ``target/release/foo``, ``build/x``, ``zig-out/bin/x``,
    ``node_modules/.bin/foo``, ``/tmp/x`` …) is invoked by direct file
    path and bypasses ``PATH`` entirely — these are project artifacts,
    not tools the user has to install.

    For each command in the group:
      1. Tokenize with ``shlex``.
      2. Split on ``&&``/``||``/``;``/``|`` to find sub-command starts.
      3. Skip env-var assignments (``FOO=bar cmd``) and known builtins.
      4. Skip any token containing ``/`` — it's a path, not a PATH lookup.
      5. Record the first qualifying token of each sub-command as a
         required executable.
    """
    executables: dict[str, str] = {}

    for command in group:
        tokens = _split_tokens(command)

        expect_command = True
        for token in tokens:
            if token in _SHELL_OPERATORS:
                expect_command = True
                continue
            if not expect_command:
                continue
            # Env-var assignments (FOO=bar cmd ...) — keep looking.
            if "=" in token and not token.startswith("="):
                continue
            # Shell builtins consume the command position but need no PATH.
            if token in _SHELL_BUILTINS:
                expect_command = False
                continue
            # Path-based invocations bypass PATH; treat the position as
            # consumed and move on.
            if "/" in token:
                expect_command = False
                continue
            executables.setdefault(token, command)
            expect_command = False

    return executables


def check_tool_availability(plan: Plan, config: OssatureConfig, console: Console) -> bool:
    groups = _command_groups_from_plan(plan, config)
    if not groups:
        return True

    # exe -> ordered, deduplicated list of originating command strings
    missing: dict[str, list[str]] = {}

    for group in groups:
        for exe, cmd in _extract_executables_for_group(group).items():
            if shutil.which(exe):
                continue
            cmds = missing.setdefault(exe, [])
            if cmd not in cmds:
                cmds.append(cmd)

    if not missing:
        return True

    console.print()
    console.print("[bold red]Missing required tools[/bold red]")
    console.print()
    for exe in sorted(missing):
        console.print(f"  [red]x[/red] [bold]{exe}[/bold] not found on PATH")
        for cmd in missing[exe]:
            console.print(f"    used by: [dim]{cmd}[/dim]")
    console.print()
    console.print("Install the missing tools before running the build to avoid wasting LLM tokens.")
    return False
