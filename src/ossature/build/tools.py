"""Sandboxed file and command tools for build agents."""

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, RunContext
from rich.console import Console
from rich.status import Status

from ossature.shared import FileEdit, apply_edits


@dataclass
class BuildContext:
    output_dir: Path
    console: Console
    status: Status
    verbose: bool = False
    context_dir: Path | None = None
    task_label: str = ""
    created_files: list[str] = field(default_factory=list)
    edited_files: list[str] = field(default_factory=list)
    total_lines: int = 0
    # Output-relative paths the agent must not write or edit. Used by verify
    # tasks to keep the author-owned fixture and the generated harness out of
    # the fixer's reach, so it cannot rewrite the grader to pass.
    protected_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.output_dir = self.output_dir.resolve()

    def set_phase(self, phase: str) -> None:
        self.status.update(f"{self.task_label} {phase}")

    def log_tool(self, message: str) -> None:
        if self.verbose:
            self.console.log(message)


def _resolve_sandboxed(output_dir: Path, path: str, console: Console) -> Path:
    resolved = (output_dir / path).resolve()
    if not resolved.is_relative_to(output_dir):
        console.log(
            f"    [red] Access denied:[/red] [bold]{path}[/bold] "
            f"→ resolves to [dim]{resolved}[/dim] (outside [dim]{output_dir}[/dim])"
        )
        raise ModelRetry(
            f"Access denied: '{path}' resolves outside the output directory '{output_dir}'. "
            f"All file operations are sandboxed to the output directory. "
            f"Use a relative path within the project (no '..' or absolute paths)."
        )
    return resolved


_SHELL_EXPANSION_PATTERN = re.compile(
    r"""
      `               # backtick substitution
    | \$\(            # $() command substitution
    | \$\{            # ${} variable expansion
    | \$[A-Za-z_]     # $VAR variable reference
    """,
    re.VERBOSE,
)


def _validate_command(command: str, output_dir: Path, console: Console) -> None:
    if _SHELL_EXPANSION_PATTERN.search(command):
        console.log(f"    [red] Command denied:[/red] [bold]{command}[/bold]")
        raise ModelRetry(
            f"Access denied: command '{command}' contains shell expansions "
            f"(backticks, $(), ${{}}, or $VAR). Use literal paths only."
        )

    try:
        tokens = shlex.split(command)
    except ValueError:
        console.log(f"    [red] Command denied:[/red] [bold]{command}[/bold]")
        raise ModelRetry(
            f"Access denied: command '{command}' could not be parsed. "
            f"Use simple commands with properly quoted arguments."
        ) from None

    resolved_output = output_dir.resolve()
    for token in tokens:
        if ".." in token.split("/"):
            resolved = (output_dir / token).resolve()
            if not resolved.is_relative_to(resolved_output):
                console.log(f"    [red] Command denied:[/red] [bold]{command}[/bold]")
                raise ModelRetry(
                    f"Access denied: '{token}' resolves outside the output directory. "
                    f"All commands are sandboxed to the output directory."
                )
        elif token.startswith("/"):
            resolved = Path(token).resolve()
            if not resolved.is_relative_to(resolved_output):
                console.log(f"    [red] Command denied:[/red] [bold]{command}[/bold]")
                raise ModelRetry(
                    f"Access denied: '{token}' is outside the output directory. "
                    f"All commands are sandboxed to the output directory. "
                    f"Use relative paths, or absolute paths within '{output_dir}'."
                )


# The fixtures directory is Ossature-owned: it holds the serialized
# author-written verification cases. No agent may write there, ever.
_FIXTURES_DIR_NAME = "checks"


def _check_writable(ctx: RunContext[BuildContext], path: str, full_path: Path) -> None:
    protected = {(ctx.deps.output_dir / p).resolve() for p in ctx.deps.protected_paths}
    fixtures_dir = (ctx.deps.output_dir / _FIXTURES_DIR_NAME).resolve()
    if full_path in protected or full_path.is_relative_to(fixtures_dir):
        ctx.deps.console.log(f"    [red] Write denied:[/red] [bold]{path}[/bold] (read-only)")
        raise ModelRetry(
            f"Access denied: '{path}' is a generated verification file and is "
            f"read-only. The verification cases are author-owned; fix the "
            f"implementation instead of the tests."
        )


def _register_tools(agent: Agent[BuildContext, str]) -> None:
    @agent.tool
    def write_file(ctx: RunContext[BuildContext], path: str, content: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        _check_writable(ctx, path, full_path)
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
        except OSError as e:
            return f"Error writing {path}: {e}"
        is_new = path not in ctx.deps.created_files
        if is_new:
            ctx.deps.created_files.append(path)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        ctx.deps.total_lines += line_count
        action = "wrote" if is_new else "updated"
        ctx.deps.set_phase(f"-- {action} {path}")
        ctx.deps.log_tool(f"      {action} [bold]{path}[/bold] ({line_count} lines)")
        return f"Written: {path} ({len(content)} bytes, {line_count} lines)"

    @agent.tool
    def edit_file(ctx: RunContext[BuildContext], path: str, edits: list[FileEdit]) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        _check_writable(ctx, path, full_path)
        try:
            if not full_path.exists():
                raise ModelRetry(
                    f"Cannot edit '{path}': file does not exist. "
                    f"Use `write_file` to create new files."
                )
            content = full_path.read_text()
        except OSError as e:
            return f"Error reading {path}: {e}"

        updated = apply_edits(content, edits)
        try:
            full_path.write_text(updated)
        except OSError as e:
            return f"Error writing {path}: {e}"

        if path not in ctx.deps.created_files and path not in ctx.deps.edited_files:
            ctx.deps.edited_files.append(path)

        ctx.deps.set_phase(f"-- edited {path}")
        ctx.deps.log_tool(f"      edited [bold]{path}[/bold] ({len(edits)} edit(s))")
        return f"Edited: {path} ({len(edits)} edit(s) applied)"

    @agent.tool
    def read_file(ctx: RunContext[BuildContext], path: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.set_phase(f"-- reading {path}")
            return full_path.read_text()
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def read_lines(ctx: RunContext[BuildContext], path: str, start_line: int, end_line: int) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.set_phase(f"-- reading {path}:{start_line}-{end_line}")
            lines = full_path.read_text().splitlines()
            total = len(lines)
            start = max(1, start_line) - 1
            end = min(total, end_line)
            selected = lines[start:end]
            numbered = [f"{i + start + 1}: {line}" for i, line in enumerate(selected)]
            return f"Lines {start + 1}-{end} of {total}:\n" + "\n".join(numbered)
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def grep_file(ctx: RunContext[BuildContext], path: str, pattern: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.set_phase(f"-- searching {path}")
            lines = full_path.read_text().splitlines()
            compiled = re.compile(pattern, re.IGNORECASE)
            matches: list[str] = []
            for i, line in enumerate(lines):
                if compiled.search(line):
                    # Include 1 line of context above and below
                    start = max(0, i - 1)
                    end = min(len(lines), i + 2)
                    for j in range(start, end):
                        prefix = ">" if j == i else " "
                        entry = f"{prefix} {j + 1}: {lines[j]}"
                        if entry not in matches:
                            matches.append(entry)
                    matches.append("---")
            if not matches:
                return f"No matches for '{pattern}' in {path}"
            return f"Matches in {path}:\n" + "\n".join(matches[:200])
        except re.error as e:
            return f"Invalid pattern '{pattern}': {e}"
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def list_files(ctx: RunContext[BuildContext], directory: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, directory, ctx.deps.console)
        try:
            if not full_path.is_dir():
                return f"Error: {directory} is not a directory"
            ctx.deps.set_phase(f"-- listing {directory}")
            max_entries = 200
            entries = sorted(full_path.iterdir())
            result: list[str] = []
            for entry in entries[:max_entries]:
                rel = entry.relative_to(ctx.deps.output_dir)
                if entry.is_dir():
                    result.append(f"  {rel}/")
                else:
                    size = entry.stat().st_size
                    result.append(f"  {rel} ({size} bytes)")
            if len(entries) > max_entries:
                result.append(f"  ... and {len(entries) - max_entries} more entries (truncated)")
            return "\n".join(result) if result else f"{directory} is empty"
        except OSError as e:
            return f"Error listing {directory}: {e}"

    @agent.tool
    def run_command(ctx: RunContext[BuildContext], command: str) -> str:
        _validate_command(command, ctx.deps.output_dir, ctx.deps.console)
        ctx.deps.set_phase(f"-- running: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=str(ctx.deps.output_dir),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 120 seconds"
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr
        return f"Exit code: {result.returncode}\n{output}"

    @agent.tool
    def copy_context_file(ctx: RunContext[BuildContext], context_path: str, dest_path: str) -> str:
        """Copy a file from the context directory to the output directory."""
        if ctx.deps.context_dir is None:
            return "Error: no context directory configured for this project"
        src = (ctx.deps.context_dir / context_path).resolve()
        if not src.is_relative_to(ctx.deps.context_dir.resolve()):
            raise ModelRetry(
                f"Access denied: '{context_path}' resolves outside the context directory. "
                f"Use a relative path within the context directory."
            )
        if not src.exists():
            return f"Error: context file '{context_path}' does not exist"
        dest = _resolve_sandboxed(ctx.deps.output_dir, dest_path, ctx.deps.console)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))
        except OSError as e:
            return f"Error copying {context_path} to {dest_path}: {e}"
        if dest_path not in ctx.deps.created_files:
            ctx.deps.created_files.append(dest_path)
        ctx.deps.set_phase(f"-- copied context:{context_path} → {dest_path}")
        ctx.deps.log_tool(f"      copied [bold]{context_path}[/bold] → [bold]{dest_path}[/bold]")
        return f"Copied: {context_path} → {dest_path}"

    @agent.tool
    def read_context_file(ctx: RunContext[BuildContext], context_path: str) -> str:
        """Read a text file from the context directory."""
        if ctx.deps.context_dir is None:
            return "Error: no context directory configured for this project"
        src = (ctx.deps.context_dir / context_path).resolve()
        if not src.is_relative_to(ctx.deps.context_dir.resolve()):
            raise ModelRetry(
                f"Access denied: '{context_path}' resolves outside the context directory. "
                f"Use a relative path within the context directory."
            )
        if not src.exists():
            return f"Error: context file '{context_path}' does not exist"
        ctx.deps.set_phase(f"-- reading context:{context_path}")
        try:
            return src.read_text()
        except UnicodeDecodeError:
            return f"Error: '{context_path}' is a binary file — use copy_context_file instead"
        except OSError as e:
            return f"Error reading context file '{context_path}': {e}"
