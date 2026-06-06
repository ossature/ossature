import json
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import content_types
import tomli_w
from pydantic_ai import Agent, ModelRetry, RunContext, capture_run_messages
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, RetryPromptPart
from pydantic_ai.usage import UsageLimits
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from ossature.audit.planner import write_plan
from ossature.build.copy import assemble_copy_task_prompt, build_copy_task
from ossature.build.state import (
    TaskState,
    compute_input_hash,
    compute_output_hash,
    get_task_created_files,
    load_state,
    make_task_slug,
    write_state,
)
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.plan import Plan, PlanTask, TaskStatus
from ossature.models.smd import SMDSpec
from ossature.promptspec import render
from ossature.renderer.amd import render_component, render_data_model, render_dependency
from ossature.renderer.smd import render_example, render_requirement
from ossature.shared import FileEdit, apply_edits
from ossature.shared.llm import UsageTracker

_MAX_NOOP_RETRIES: int = 2


class BuildMode(Enum):
    DEFAULT = "default"  # Pause on failure
    STEP = "step"  # Pause after every task
    AUTO = "auto"  # Run to completion, stop on failure
    AUTO_SKIP = "auto_skip"  # Run everything possible, skip failures


# Build context & tools


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


def _register_tools(agent: Agent[BuildContext, str]) -> None:
    @agent.tool
    def write_file(ctx: RunContext[BuildContext], path: str, content: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
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


def _create_impl_agent(config: OssatureConfig) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        config.llm.model_for("build"),
        system_prompt=render("build.implementer", language=config.output.language),
        deps_type=BuildContext,
        retries=config.llm.tool_retries,
        model_settings={"max_tokens": config.build.max_output_tokens},
    )
    _register_tools(agent)
    return agent


def _create_fix_agent(config: OssatureConfig) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        config.llm.model_for("build"),
        system_prompt=render("build.fixer", language=config.output.language),
        deps_type=BuildContext,
        retries=config.llm.tool_retries,
        model_settings={"max_tokens": config.build.max_output_tokens},
    )
    _register_tools(agent)
    return agent


# Agent run retry

_STRUCTURAL_ERROR_PATTERNS: tuple[str, ...] = (
    "missing key",
    "is not an object",
    "Expected a JSON array",
    "Could not parse edits JSON",
    "must both be strings",
    "Field required",
    "validation error",
)

_EDIT_SCHEMA_REMINDER: str = (
    "\n\n<important>\n"
    "IMPORTANT: When using `edit_file`, the `edits` parameter must be a list of objects "
    'with exactly two keys: "old" and "new". Example:\n'
    'edit_file(path="src/main.py", edits=[{"old": "text to find", "new": "replacement"}])\n'
    "Do NOT use key names like 'old_str', 'new_str', 'search', 'replace', or any variant.\n"
    "</important>"
)


def _extract_last_retry_error(messages: list[Any]) -> str | None:
    """Walk captured messages backwards to find the last tool-retry error content."""
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, RetryPromptPart) and isinstance(part.content, str):
                return part.content
    return None


def _is_structural_tool_error(detail: str | None) -> bool:
    """Check if a retry error indicates structural schema confusion (not content errors)."""
    if not detail:
        return False
    detail_lower = detail.lower()
    return any(p.lower() in detail_lower for p in _STRUCTURAL_ERROR_PATTERNS)


def _run_with_retry(
    agent: Agent[BuildContext, str],
    prompt: str,
    deps: BuildContext,
    console: Console,
    max_retries: int = 5,
    base_delay: float = 30.0,
    tracker: UsageTracker | None = None,
    model_name: str | None = None,
) -> Any:
    _structural_retried = False
    for attempt in range(max_retries):
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    prompt, deps=deps, usage_limits=UsageLimits(request_limit=200)
                )
                if tracker is not None:
                    tracker.add(result.usage(), model_name=model_name)
                return result
            except json.JSONDecodeError:
                if attempt >= max_retries - 1:
                    raise
                delay = base_delay * (2**attempt)
                console.log(
                    f"    [yellow]Malformed API response, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})[/yellow]"
                )
                time.sleep(delay)
            except ModelHTTPError as e:
                if e.status_code != 429 or attempt >= max_retries - 1:
                    raise
                delay = base_delay * (2**attempt)
                console.log(
                    f"    [yellow] Rate limited, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})[/yellow]"
                )
                time.sleep(delay)
            except AgentRunError as e:
                detail = _extract_last_retry_error(messages)
                if _is_structural_tool_error(detail) and not _structural_retried:
                    _structural_retried = True
                    console.log(
                        "    [yellow]Structural tool-call error — "
                        "retrying with fresh context[/yellow]"
                    )
                    prompt = prompt + _EDIT_SCHEMA_REMINDER
                    continue
                if detail:
                    e._last_retry_detail = detail  # type: ignore[attr-defined]
                raise
    raise RuntimeError("Unreachable")


# Section filtering


def _render_spec_ref(smd: SMDSpec, section: str) -> str | None:
    s = section.lower()

    if s == "overview":
        return f"### Overview\n\n{smd.overview}"

    if s == "goals" and smd.goals:
        return "### Goals\n\n" + "\n".join(f"- {g}" for g in smd.goals)

    if s == "non-goals" and smd.non_goals:
        return "### Non-Goals\n\n" + "\n".join(f"- {g}" for g in smd.non_goals)

    if s == "constraints" and smd.constraints:
        return "### Constraints\n\n" + "\n".join(f"- {c}" for c in smd.constraints)

    if s == "acceptance criteria" and smd.acceptance_criteria:
        return "### Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in smd.acceptance_criteria)

    if s == "notes" and smd.notes:
        return f"### Notes\n\n{smd.notes}"

    if s == "requirements" and smd.requirements:
        rendered = "\n\n".join(render_requirement(r) for r in smd.requirements)
        return f"## Requirements\n\n{rendered}"

    if s == "examples" and smd.examples:
        rendered = "\n\n".join(render_example(e) for e in smd.examples)
        return f"## Examples\n\n{rendered}"

    # Match individual requirement by title
    for req in smd.requirements:
        if req.title.lower() == s:
            return render_requirement(req)

    # Match individual example by name
    for ex in smd.examples:
        if ex.name.lower() == s:
            return render_example(ex)

    return None


def _render_arch_ref(amds: list[AMDSpec], section: str) -> str | None:
    s = section.lower()

    if s == "overview":
        parts = [a.overview for a in amds if a.overview]
        return ("### Overview\n\n" + "\n\n".join(parts)) if parts else None

    if s == "dependencies":
        deps = [d for a in amds for d in a.dependencies]
        if not deps:
            return None
        return "### Dependencies\n\n" + "\n".join(render_dependency(d) for d in deps)

    if s == "flow":
        parts = [a.flow for a in amds if a.flow]
        if not parts:
            return None
        return "### Flow\n\n```\n" + "\n\n".join(parts) + "\n```"

    if s == "notes":
        parts = [a.notes for a in amds if a.notes]
        return ("### Notes\n\n" + "\n\n".join(parts)) if parts else None

    if s.startswith("components >"):
        name = section.split(">", 1)[1].strip()
        for amd in amds:
            for comp in amd.components:
                if comp.name.lower() == name.lower():
                    return render_component(comp)
        return None

    if s.startswith("data models >"):
        name = section.split(">", 1)[1].strip()
        for amd in amds:
            for dm in amd.data_models:
                if dm.name.lower() == name.lower():
                    return render_data_model(dm)
        return None

    return None


# Prompt assembly


def assemble_task_prompt(
    task: PlanTask,
    config: OssatureConfig,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
) -> str:
    sections: list[str] = []

    # Project config
    config_lines = [
        f"Project: {config.name} v{config.version}",
        f"Language: {config.output.language}",
    ]
    if config.output.framework:
        config_lines.append(f"Framework: {config.output.framework}")
    sections.append("<project_config>\n" + "\n".join(config_lines) + "\n</project_config>")

    # Project brief
    brief_path = config.metadata_context_path / "project-brief.md"
    if brief_path.exists():
        sections.append(f"<project_brief>\n{brief_path.read_text().strip()}\n</project_brief>")

    # Spec brief
    spec_brief_path = config.metadata_context_spec_briefs_path / f"{task.spec}.md"
    if spec_brief_path.exists():
        sections.append(
            f'<spec_brief spec="{task.spec}">\n{spec_brief_path.read_text().strip()}\n</spec_brief>'
        )

    # Filtered spec sections (via spec_refs)
    smd = smd_map.get(task.spec)
    if smd and task.spec_refs:
        spec_parts: list[str] = []
        for ref in task.spec_refs:
            rendered = _render_spec_ref(smd, ref.strip())
            if rendered:
                spec_parts.append(rendered)
        if spec_parts:
            sections.append(
                "<specification_context>\n" + "\n\n".join(spec_parts) + "\n</specification_context>"
            )

    # Filtered arch sections (via arch_refs)
    amds = amd_by_spec.get(task.spec)
    if amds and task.arch_refs:
        arch_parts: list[str] = []
        for ref in task.arch_refs:
            rendered = _render_arch_ref(amds, ref.strip())
            if rendered:
                arch_parts.append(rendered)
        if arch_parts:
            sections.append(
                "<architecture_context>\n" + "\n\n".join(arch_parts) + "\n</architecture_context>"
            )

    # Inject files — list available dependency files for tool-based exploration.
    # Only file names are listed (no line counts or sizes) so the prompt text
    # stays stable when later tasks edit these files.
    if task.inject_files:
        available: list[str] = []
        for filepath in task.inject_files:
            full_path = config.output_path / filepath
            if full_path.exists():
                available.append(f"- `{filepath}`")
        if available:
            sections.append(
                "<dependency_files>\n"
                "The following files from previous tasks are available. "
                "Use `grep_file` and `read_lines` to inspect the types, "
                "interfaces, and signatures you need.\n\n"
                + "\n".join(available)
                + "\n</dependency_files>"
            )

    # Cross-spec interfaces
    if task.cross_spec_interfaces:
        iface_sections: list[str] = []
        for spec_id in task.cross_spec_interfaces:
            iface_path = config.metadata_context_interfaces_path / f"{spec_id}.md"
            if iface_path.exists():
                iface_sections.append(
                    f'<interface spec="{spec_id}">\n{iface_path.read_text().strip()}\n</interface>'
                )
        if iface_sections:
            sections.append(
                "<cross_spec_interfaces>\n"
                + "\n\n".join(iface_sections)
                + "\n</cross_spec_interfaces>"
            )

    # Context files
    if task.context_files:
        context_file_entries: list[str] = []
        for cf in task.context_files:
            cf_path = config.context_path / cf
            if not cf_path.exists():
                context_file_entries.append(f"- `{cf}` — not found")
                continue
            mime_type = content_types.get_content_type(cf_path.name)
            size = cf_path.stat().st_size
            is_text = mime_type.startswith("text/") or mime_type in {
                "application/json",
                "application/xml",
                "application/toml",
                "application/yaml",
            }
            if is_text:
                try:
                    content = cf_path.read_text()
                    context_file_entries.append(
                        f"### {cf}\n\n"
                        f"**MIME type:** `{mime_type}` ({size} bytes)\n\n"
                        f"```\n{content}\n```"
                    )
                except UnicodeDecodeError:
                    context_file_entries.append(
                        f"- `{cf}` — `{mime_type}` ({size} bytes) — "
                        f"use `read_context_file` or `copy_context_file` to access"
                    )
            else:
                context_file_entries.append(f"- `{cf}` — `{mime_type}` ({size} bytes)")

        if context_file_entries:
            sections.append(
                "<context_files>\n"
                "The following files from the project's context directory are assigned "
                "to this task. Use `copy_context_file(context_path, dest_path)` to copy "
                "assets into the appropriate location within the output directory "
                "(choose a destination path that fits the project structure, e.g. "
                "`assets/audio/music.mp3` or `sounds/correct.wav`). "
                "Use `read_context_file(context_path)` to read text files on demand.\n\n"
                + "\n\n".join(context_file_entries)
                + "\n</context_files>"
            )

    # Task description — placed last so the query follows all context
    task_block = f"<task>\n## {task.title}\n\n{task.description}"
    if task.notes:
        task_block += f"\n\n**Notes:** {task.notes}"
    if task.outputs:
        outputs_list = "\n".join(f"- `{o}`" for o in task.outputs)
        task_block += f"\n\n## Files to Produce\n\n{outputs_list}"
    task_block += "\n</task>"
    sections.append(task_block)

    return "\n\n".join(sections)


def assemble_fix_prompt(
    task: PlanTask, error_output: str, config: OssatureConfig, verify_command: str = ""
) -> str:
    sections = [f"<error_output>\n```\n{error_output}\n```\n</error_output>"]

    if verify_command:
        sections.append(f"<verify_command>\n{verify_command}\n</verify_command>")

    # Include output files, falling back to inject_files for modify-in-place tasks.
    # Files in task.outputs that don't exist on disk are filtered out upstream by
    # build_task, which short-circuits to a missing-outputs failure rather than
    # entering the fix loop. So here we only see files that actually exist (or
    # inject_files that may legitimately not exist for unusual modify-in-place flows).
    file_list = task.outputs if task.outputs else (task.inject_files or [])
    for filepath in file_list:
        full_path = config.output_path / filepath
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text()
        except UnicodeDecodeError:
            continue

        line_count = content.count("\n") + 1
        if line_count > config.build.max_inline_lines:
            sections.append(
                f'<current_file path="{filepath}" total_lines="{line_count}">\n'
                f"File is large. Use `read_lines` or `grep_file` to inspect "
                f"the regions referenced in the error output above.\n"
                f"</current_file>"
            )
        else:
            sections.append(
                f'<current_file path="{filepath}">\n```\n{content}\n```\n</current_file>'
            )

    sections.append(f"<task>\n**{task.title}**: {task.description}\n</task>")

    return "\n\n".join(sections)


# Verification


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


# Task building


def save_task_output(
    task_dir: Path,
    created_files: list[str],
    edited_files: list[str],
    success: bool,
    verify_output: str,
) -> None:
    data: dict[str, Any] = {
        "created_files": created_files,
        "success": success,
        "verify_output": verify_output,
    }
    if edited_files:
        data["edited_files"] = edited_files
    with open(task_dir / "output.toml", "wb") as f:
        tomli_w.dump(data, f)


def extract_spec_interface(
    spec_id: str,
    plan: Plan,
    config: OssatureConfig,
    console: Console,
    status: Status,
    tracker: UsageTracker | None = None,
) -> None:
    source_files: list[tuple[str, str]] = []
    for task in plan.tasks:
        if task.spec != spec_id or task.status != TaskStatus.DONE:
            continue
        if task.source:
            # Copy tasks ship verbatim assets (often binary). They have no
            # generated-source interface to extract.
            continue
        for filepath in task.outputs:
            full_path = config.output_path / filepath
            if not full_path.exists():
                continue
            try:
                source_files.append((filepath, full_path.read_text()))
            except OSError, UnicodeDecodeError:
                continue

    if not source_files:
        return

    language = config.output.language
    sections = [f"# Source files for {spec_id}\n"]
    for filepath, content in source_files:
        sections.append(f"## {filepath}\n\n```{language}\n{content}\n```\n")

    status.update(f"Extracting interface: {spec_id}")
    console.log(f"  [cyan]Extracting interface for {spec_id}...[/cyan]")

    model = config.llm.model_for("interface")
    agent = Agent(
        model,
        instructions=render("build.interface_extraction", language=language),
        retries=config.llm.retries,
    )
    result = agent.run_sync("\n".join(sections))
    if tracker is not None:
        tracker.add(result.usage(), model_name=model)

    interface_content = f"# Interface: {spec_id}\n\n@source: build\n\n{result.output}"

    iface_dir = config.metadata_context_interfaces_path
    iface_dir.mkdir(parents=True, exist_ok=True)
    (iface_dir / f"{spec_id}.md").write_text(interface_content)

    console.log(f"  [green]Interface written: .ossature/context/interfaces/{spec_id}.md[/green]")


def _truncate_output(text: str, max_lines: int = 30) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = [*lines[:10], f"  ... ({len(lines) - 20} lines omitted) ...", *lines[-10:]]
    return "\n".join(kept)


def _print_verify_errors(console: Console, verify_output: str) -> None:
    truncated = _truncate_output(verify_output)
    console.print()
    console.print(
        Panel(
            truncated,
            title="[bold red]Errors[/bold red]",
            border_style="red",
            expand=True,
            padding=(0, 1),
        )
    )


def _format_verify_for_display(commands: list[str]) -> str:
    """Render a verify command list as a single string for status/error messages."""
    if not commands:
        return ""
    if len(commands) == 1:
        return commands[0]
    return " && ".join(commands)


def _print_verify_command_error(console: Console, task: PlanTask, verify_output: str) -> None:
    truncated = _truncate_output(verify_output)
    body = (
        f"The verify command itself appears to be invalid — this is not a code error.\n\n"
        f"  Command: [bold]{_format_verify_for_display(task.verify)}[/bold]\n\n"
        f"{truncated}\n\n"
        f"Update the [cyan]verify[/cyan] field for task [bold]{task.id}[/bold] "
        "in [cyan].ossature/plan.toml[/cyan], then run "
        f"[cyan]ossature retry --only {task.id}[/cyan]."
    )
    console.print()
    console.print(
        Panel(
            body,
            title="[bold yellow]Invalid Verify Command[/bold yellow]",
            border_style="yellow",
            expand=False,
            box=box.ROUNDED,
        )
    )


def _print_missing_outputs_error(console: Console, task: PlanTask, missing: list[str]) -> None:
    missing_lines = "\n".join(f"  - {f}" for f in missing)
    body = (
        "The implementer did not produce the files this task is supposed to create. "
        "The fix loop won't run because the fixer doesn't have the spec/architecture "
        "context the original implementer had, so it can't faithfully write the missing "
        "files from scratch.\n\n"
        f"Missing outputs:\n{missing_lines}\n\n"
        f"Investigate [cyan].ossature/tasks/{task.id}-*/[/cyan] to see what the "
        "implementer returned. You can simplify the task description, switch model, "
        f"or just retry with [cyan]ossature retry --only {task.id}[/cyan]."
    )
    console.print()
    console.print(
        Panel(
            body,
            title="[bold red]Missing Outputs[/bold red]",
            border_style="red",
            expand=False,
            box=box.ROUNDED,
        )
    )


@dataclass
class TaskResult:
    success: bool
    file_count: int = 0
    total_lines: int = 0
    elapsed: float = 0.0
    created_files: list[str] = field(default_factory=list)
    edited_files: list[str] = field(default_factory=list)
    usage: UsageTracker = field(default_factory=UsageTracker)

    def summary(self) -> str:
        parts = []
        if self.file_count:
            files_word = "file" if self.file_count == 1 else "files"
            parts.append(f"{self.file_count} {files_word}")
        if self.total_lines:
            parts.append(f"{self.total_lines} lines")
        parts.append(f"{self.elapsed:.1f}s")
        parts.append(self.usage.format_usage())
        return ", ".join(parts)


class BuildBackend(Protocol):
    def generate(
        self,
        prompt: str,
        ctx: BuildContext,
        console: Console,
        tracker: UsageTracker,
        model_name: str,
    ) -> str: ...

    def fix(
        self,
        prompt: str,
        ctx: BuildContext,
        console: Console,
        tracker: UsageTracker,
        model_name: str,
    ) -> str: ...

    def verify(self, commands: list[str], cwd: Path) -> tuple[bool, str]: ...


class DefaultBuildBackend:
    def __init__(self, config: OssatureConfig) -> None:
        self._config = config

    def generate(
        self,
        prompt: str,
        ctx: BuildContext,
        console: Console,
        tracker: UsageTracker,
        model_name: str,
    ) -> str:
        agent = _create_impl_agent(self._config)
        result = _run_with_retry(
            agent, prompt, ctx, console, tracker=tracker, model_name=model_name
        )
        output: str = result.output
        return output

    def fix(
        self,
        prompt: str,
        ctx: BuildContext,
        console: Console,
        tracker: UsageTracker,
        model_name: str,
    ) -> str:
        agent = _create_fix_agent(self._config)
        result = _run_with_retry(
            agent, prompt, ctx, console, tracker=tracker, model_name=model_name
        )
        output: str = result.output
        return output

    def verify(self, commands: list[str], cwd: Path) -> tuple[bool, str]:
        return run_verify(commands, cwd)


def build_task(
    task: PlanTask,
    config: OssatureConfig,
    prompt: str,
    console: Console,
    status: Status,
    verbose: bool = False,
    *,
    backend: BuildBackend | None = None,
) -> TaskResult:
    backend = backend or DefaultBuildBackend(config)

    slug = make_task_slug(task)
    task_dir = config.metadata_path / "tasks" / f"{task.id}-{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "prompt.md").write_text(prompt)

    task_label = f"[{task.id}] {task.title}"

    build_ctx = BuildContext(
        output_dir=config.output_path,
        console=console,
        status=status,
        verbose=verbose,
        context_dir=config.context_path if config.context_path.is_dir() else None,
        task_label=task_label,
    )

    t0 = time.monotonic()
    task_usage = UsageTracker()
    build_model = config.llm.model_for("build")

    # Implementation. If the task expects outputs but the agent returns
    # without invoking any file-writing tool, retry with a stronger
    # reminder. Some models occasionally respond with prose like "let's
    # write game.lua now" but never call write_file.
    expects_outputs = bool(task.outputs)
    impl_prompt = prompt
    noop_attempt = 0
    while True:
        build_ctx.set_phase("-- generating...")
        files_before = set(build_ctx.created_files) | set(build_ctx.edited_files)
        gen_output = backend.generate(
            impl_prompt, build_ctx, console, tracker=task_usage, model_name=build_model
        )
        files_after = set(build_ctx.created_files) | set(build_ctx.edited_files)
        if not expects_outputs or files_after != files_before:
            break
        if noop_attempt >= _MAX_NOOP_RETRIES:
            console.log(
                f"    [yellow]Implementer made no changes after {noop_attempt + 1} "
                f"attempts, moving on[/yellow]"
            )
            break
        noop_attempt += 1
        console.log(
            f"    [yellow]Implementer made no changes (attempt {noop_attempt}), retrying[/yellow]"
        )
        impl_prompt = (
            prompt + "\n\n<important>\n"
            "You MUST use `write_file` to create the files listed in this task's "
            "outputs. Do not respond with only prose describing what you would "
            "write. Call the tool.\n"
            "</important>"
        )
    (task_dir / "response.md").write_text(gen_output)

    def _make_result(success: bool) -> TaskResult:
        return TaskResult(
            success=success,
            file_count=len(build_ctx.created_files) + len(build_ctx.edited_files),
            total_lines=build_ctx.total_lines,
            elapsed=time.monotonic() - t0,
            created_files=list(build_ctx.created_files),
            edited_files=list(build_ctx.edited_files),
            usage=task_usage,
        )

    if not task.verify:
        save_task_output(task_dir, build_ctx.created_files, build_ctx.edited_files, True, "")
        return _make_result(True)

    verify_label = _format_verify_for_display(task.verify)

    # Verification
    build_ctx.set_phase(f"-- verifying ({verify_label})")
    passed, verify_output = backend.verify(task.verify, config.output_path)

    if passed:
        save_task_output(
            task_dir, build_ctx.created_files, build_ctx.edited_files, True, verify_output
        )
        return _make_result(True)

    # Check if the error is a command invocation problem, not a code problem
    if is_verify_command_error(verify_output, config.output_path):
        _print_verify_command_error(console, task, verify_output)
        save_task_output(
            task_dir, build_ctx.created_files, build_ctx.edited_files, False, verify_output
        )
        return _make_result(False)

    # If any expected outputs are missing on disk, skip the fix loop. The
    # fixer only sees the verify error, the current file contents, and the
    # task title/description. It doesn't have the spec/arch/inject context
    # the implementer had, so it can't faithfully write missing files from
    # scratch. The noop retry already gave the implementer multiple chances.
    missing_outputs = [f for f in task.outputs if not (config.output_path / f).exists()]
    if missing_outputs:
        _print_missing_outputs_error(console, task, missing_outputs)
        save_task_output(
            task_dir, build_ctx.created_files, build_ctx.edited_files, False, verify_output
        )
        return _make_result(False)

    # Fix loop — fresh agent per attempt to avoid accumulating fix history
    noop_count = 0
    attempt = 0
    while attempt < config.build.max_fix_attempts:
        build_ctx.set_phase(f"-- fixing ({attempt + 1}/{config.build.max_fix_attempts})")
        fix_prompt = assemble_fix_prompt(task, verify_output, config, verify_label)
        (task_dir / f"fix-{attempt + 1}-prompt.md").write_text(fix_prompt)

        # Snapshot file lists to detect no-op responses
        files_before = set(build_ctx.created_files) | set(build_ctx.edited_files)

        try:
            fix_output = backend.fix(
                fix_prompt, build_ctx, console, tracker=task_usage, model_name=build_model
            )
        except AgentRunError as e:
            console.log(
                f"    [yellow]Fixer agent error on attempt {attempt + 1}: {e.message}[/yellow]"
            )
            (task_dir / f"fix-{attempt + 1}-response.md").write_text(f"[agent error] {e.message}")
            attempt += 1
            continue

        (task_dir / f"fix-{attempt + 1}-response.md").write_text(fix_output)

        # Detect no-op: fixer made no file changes
        files_after = set(build_ctx.created_files) | set(build_ctx.edited_files)
        if files_after == files_before:
            noop_count += 1
            if noop_count <= _MAX_NOOP_RETRIES:
                console.log(
                    f"    [yellow]Fixer made no changes (attempt {attempt + 1}), retrying[/yellow]"
                )
                # Don't count this against max_fix_attempts
                fix_prompt = (
                    fix_prompt + "\n\n<important>\n"
                    "You MUST use edit_file or write_file to fix the errors. "
                    "Do not respond with only text.\n"
                    "</important>"
                )
                (task_dir / f"fix-{attempt + 1}-prompt.md").write_text(fix_prompt)
                continue
            else:
                console.log(
                    f"    [yellow]Fixer made no changes after {noop_count} "
                    f"retries, moving on[/yellow]"
                )
                attempt += 1
                continue

        build_ctx.set_phase(f"-- re-verifying ({verify_label})")
        passed, verify_output = backend.verify(task.verify, config.output_path)
        if passed:
            save_task_output(
                task_dir, build_ctx.created_files, build_ctx.edited_files, True, verify_output
            )
            return _make_result(True)
        attempt += 1

    # Only show errors after all fix attempts exhausted
    _print_verify_errors(console, verify_output)
    save_task_output(
        task_dir, build_ctx.created_files, build_ctx.edited_files, False, verify_output
    )
    return _make_result(False)


# Console output helpers


def _print_task_header(console: Console, task: PlanTask, total: int, verbose: bool = False) -> None:
    if verbose:
        console.print()
        header = Text()
        header.append(f"  [{task.id}/{total:03d}] ", style="bold cyan")
        header.append(task.title, style="bold")
        console.print(header)
        console.print(f"    [dim]{task.description}[/dim]")
        if task.outputs:
            console.print(f"    [dim]-> {', '.join(task.outputs)}[/dim]")


# Interactive prompts


def _prompt_after_success(console: Console) -> str:
    console.print()
    console.print("  [dim]Press ENTER to continue, 's' to skip next, 'q' to stop[/dim]")
    try:
        response = input("  > ").strip().lower()
    except EOFError, KeyboardInterrupt:
        return "quit"
    if response == "q":
        return "quit"
    if response == "s":
        return "skip"
    return "continue"


def _prompt_after_failure(console: Console, task: PlanTask) -> str:
    console.print()
    console.print(r"  [dim]\[R]etry task  \[s]kip  \[q]uit[/dim]")
    try:
        response = input("  > ").strip().lower()
    except EOFError, KeyboardInterrupt:
        return "quit"
    if response == "r":
        return "retry"
    if response == "s":
        return "skip"
    return "quit"


# LLM error handling


def _describe_llm_error(e: AgentRunError) -> tuple[str, str]:
    if isinstance(e, ModelHTTPError):
        status = e.status_code
        if status == 402:
            return (
                f"Insufficient API credits (HTTP {status})",
                "Refill credits and retry.",
            )
        if status == 429:
            return (
                f"Rate limited (HTTP {status})",
                "Rate limit retries exhausted. Wait and retry.",
            )
        if status >= 500:
            return (
                f"API server error (HTTP {status})",
                "The provider may be experiencing issues. Wait and retry.",
            )
        return (
            f"API error (HTTP {status})",
            "Check your API configuration and retry.",
        )
    if isinstance(e, UsageLimitExceeded):
        return (
            "Request limit exceeded",
            "The task exceeded the maximum number of LLM requests.",
        )
    return (e.message, "Check the error and retry.")


def _format_llm_error_body(e: AgentRunError) -> str | None:
    if isinstance(e, ModelHTTPError) and e.body:
        body = e.body
        if isinstance(body, dict):
            msg = (
                body.get("error", {}).get("message")
                if isinstance(body.get("error"), dict)
                else None
            )
            return msg or str(body)
        return str(body)
    return None


def _print_llm_error(console: Console, task: PlanTask, total: int, e: AgentRunError) -> None:
    summary, suggestion = _describe_llm_error(e)
    console.print()
    console.log(f"  [red]x [{task.id}/{total:03d}] {task.title}[/red]")

    lines = [summary]
    body = _format_llm_error_body(e)
    if body:
        lines.append(f"\n{body}")

    detail = getattr(e, "_last_retry_detail", None)
    if detail:
        lines.append(f"\n[dim]Last error:[/dim] {detail}")

    lines.append(f"\n{suggestion}")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold red]LLM Error[/bold red]",
            border_style="red",
            expand=False,
            box=box.ROUNDED,
        )
    )


# Setup & tool availability


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
    if config.build.test:
        groups.append(list(config.build.test))
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


# Main build loop


def execute_build(
    config: OssatureConfig,
    plan: Plan,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    console: Console,
    plan_filepath: Path,
    mode: BuildMode = BuildMode.DEFAULT,
    verbose: bool = False,
) -> None:
    config.output_path.mkdir(parents=True, exist_ok=True)

    # Check tool availability before spending LLM tokens
    if not check_tool_availability(plan, config, console):
        raise SystemExit(1)

    # Run setup command before first task (only on fresh builds)
    state_filepath = config.metadata_path / "state.toml"
    has_prior_state = state_filepath.exists() and state_filepath.stat().st_size > 0
    has_completed = has_prior_state or any(t.status == TaskStatus.DONE for t in plan.tasks)
    if not has_completed and not run_setup(config, console):
        raise SystemExit(1)

    total = plan.meta.total_tasks
    completed_before = sum(1 for t in plan.tasks if t.status == TaskStatus.DONE)
    skip_next = False
    stopped = False
    total_usage = UsageTracker()

    # Load build state for input/output hash verification
    state = load_state(state_filepath)
    tasks_dir = config.metadata_path / "tasks"

    # Precompute spec groupings for interface extraction barriers
    tasks_by_spec: dict[str, list[PlanTask]] = {}
    for t in plan.tasks:
        tasks_by_spec.setdefault(t.spec, []).append(t)
    spec_last_task_id: dict[str, str] = {}
    for t in plan.tasks:
        spec_last_task_id[t.spec] = t.id

    # Track which specs already have interface files and which were rebuilt
    extracted_interfaces: set[str] = set()
    for sid in tasks_by_spec:
        if (config.metadata_context_interfaces_path / f"{sid}.md").exists():
            extracted_interfaces.add(sid)
    rebuilt_specs: set[str] = set()
    rebuilt_tasks: set[str] = set()

    def _maybe_extract_interface(task: PlanTask, status: Status) -> None:
        if task.id != spec_last_task_id.get(task.spec):
            return
        if task.spec in extracted_interfaces and task.spec not in rebuilt_specs:
            return
        if not all(t.status == TaskStatus.DONE for t in tasks_by_spec[task.spec]):
            return
        try:
            extract_spec_interface(task.spec, plan, config, console, status, tracker=total_usage)
        except AgentRunError as e:
            summary, _ = _describe_llm_error(e)
            console.log(
                f"  [yellow]Interface extraction failed for {task.spec}: {summary}[/yellow]"
            )
            return
        extracted_interfaces.add(task.spec)

    def _store_task_state(
        task: PlanTask,
        prompt: str,
        created_files: list[str],
        edited_files: list[str] | None = None,
    ) -> None:
        input_h = compute_input_hash(prompt, task, config)
        output_h = compute_output_hash(created_files, config)
        state.set(
            task.id, TaskState(input_h, output_h, list(created_files), list(edited_files or []))
        )
        write_state(state, state_filepath)

    with Status("", console=console) as status:
        for task in plan.tasks:
            if task.status == TaskStatus.SKIPPED:
                console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (skipped)[/dim]")
                continue

            if task.status == TaskStatus.DONE:
                if task.source:
                    prompt = assemble_copy_task_prompt(task, config)
                else:
                    prompt = assemble_task_prompt(task, config, smd_map, amd_by_spec)
                current_input_hash = compute_input_hash(prompt, task, config)
                stored = state.get(task.id)

                # Check if a dependency was rebuilt this run
                dep_rebuilt = any(d in rebuilt_tasks for d in task.depends_on)

                if dep_rebuilt:
                    console.log(
                        f"  [yellow][{task.id}/{total:03d}] {task.title}"
                        f" — dependency rebuilt, re-running[/yellow]"
                    )
                elif stored and stored.input_hash == current_input_hash:
                    # Input unchanged — verify output integrity
                    current_output_hash = compute_output_hash(stored.created_files, config)
                    if stored.output_hash == current_output_hash:
                        console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (done)[/dim]")
                        _maybe_extract_interface(task, status)
                        continue
                    else:
                        console.log(
                            f"  [yellow][{task.id}/{total:03d}] {task.title}"
                            f" — output modified, re-running[/yellow]"
                        )
                elif stored:
                    console.log(
                        f"  [yellow][{task.id}/{total:03d}] {task.title}"
                        f" — input changed, re-running[/yellow]"
                    )
                else:
                    # No stored state — trust DONE status, backfill hashes
                    created_files = get_task_created_files(task, tasks_dir)
                    _store_task_state(task, prompt, created_files)
                    console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (done)[/dim]")
                    _maybe_extract_interface(task, status)
                    continue

                # Stale — mark for re-run and fall through to rebuild
                task.status = TaskStatus.PENDING
                write_plan(plan, plan_filepath)

            if task.status == TaskStatus.MANUAL:
                console.log(
                    f"  [yellow][{task.id}/{total:03d}] {task.title} — MANUAL (skipping)[/yellow]"
                )
                continue

            # Handle 'skip next' from interactive prompt
            if skip_next:
                skip_next = False
                task.status = TaskStatus.SKIPPED
                write_plan(plan, plan_filepath)
                console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (skipped by user)[/dim]")
                continue

            # Check dependencies
            task_status_map = {t.id: t.status for t in plan.tasks}
            deps_ok = all(
                task_status_map.get(dep_id) == TaskStatus.DONE for dep_id in task.depends_on
            )
            if not deps_ok:
                unmet = [
                    dep_id
                    for dep_id in task.depends_on
                    if task_status_map.get(dep_id) != TaskStatus.DONE
                ]
                console.print()
                console.log(f"  [red]x [{task.id}/{total:03d}] {task.title}[/red]")
                console.log(f"    [red]Dependencies not met: {', '.join(unmet)}[/red]")
                task.status = TaskStatus.FAILED
                write_plan(plan, plan_filepath)
                if mode == BuildMode.AUTO_SKIP:
                    continue
                stopped = True
                break

            _print_task_header(console, task, total, verbose)

            # Assemble prompt once — reused for build, retry, and hash storage
            if task.source:
                prompt = assemble_copy_task_prompt(task, config)
            else:
                prompt = assemble_task_prompt(task, config, smd_map, amd_by_spec)

            # Run task with LLM error recovery
            llm_bail = False
            while True:
                try:
                    if task.source:
                        result = build_copy_task(task, config, console, status, verbose)
                    else:
                        result = build_task(task, config, prompt, console, status, verbose)
                    break
                except AgentRunError as e:
                    task.status = TaskStatus.FAILED
                    write_plan(plan, plan_filepath)
                    status.stop()
                    _print_llm_error(console, task, total, e)

                    if mode == BuildMode.AUTO_SKIP:
                        console.log(
                            f"  [red]x [{task.id}/{total:03d}] {task.title} "
                            f"(LLM error, continuing)[/red]"
                        )
                        llm_bail = True
                        status.start()
                        break

                    if mode == BuildMode.AUTO:
                        stopped = True
                        llm_bail = True
                        status.start()
                        break

                    action = _prompt_after_failure(console, task)
                    status.start()
                    if action == "retry":
                        task.status = TaskStatus.PENDING
                        write_plan(plan, plan_filepath)
                        _print_task_header(console, task, total, verbose)
                        continue
                    if action == "skip":
                        task.status = TaskStatus.SKIPPED
                        write_plan(plan, plan_filepath)
                        console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (skipped)[/dim]")
                    else:
                        stopped = True
                    llm_bail = True
                    break

            if llm_bail:
                if stopped:
                    break
                continue

            success = result.success
            total_usage += result.usage

            if success:
                task.status = TaskStatus.DONE
                console.log(
                    f"  [green]v [{task.id}/{total:03d}] {task.title}[/green]"
                    f"  [dim]({result.summary()})[/dim]"
                )
                rebuilt_specs.add(task.spec)
                rebuilt_tasks.add(task.id)
                _store_task_state(task, prompt, result.created_files, result.edited_files)
            else:
                task.status = TaskStatus.FAILED

            write_plan(plan, plan_filepath)

            if success:
                _maybe_extract_interface(task, status)

            if success and mode == BuildMode.STEP:
                status.stop()
                action = _prompt_after_success(console)
                status.start()
                if action == "quit":
                    stopped = True
                    break
                if action == "skip":
                    skip_next = True

            if not success:
                if mode == BuildMode.AUTO_SKIP:
                    console.log(
                        f"  [red]x [{task.id}/{total:03d}] {task.title} (failed, continuing)[/red]"
                    )
                    continue

                if mode == BuildMode.AUTO:
                    console.print()
                    console.print(
                        Panel(
                            f"Task [bold]{task.id}[/bold] failed after "
                            f"{config.build.max_fix_attempts} fix attempts.\n"
                            f"Review: [cyan].ossature/tasks/{task.id}-*/[/cyan]\n"
                            f"Resume: [cyan]ossature build[/cyan]",
                            title="[bold red]Build Stopped[/bold red]",
                            border_style="red",
                            expand=False,
                            box=box.ROUNDED,
                        )
                    )
                    stopped = True
                    break

                # DEFAULT and STEP: interactive failure prompt
                status.stop()
                action = _prompt_after_failure(console, task)
                status.start()
                if action == "retry":
                    task.status = TaskStatus.PENDING
                    write_plan(plan, plan_filepath)
                    _print_task_header(console, task, total, verbose)
                    try:
                        retry_result = build_task(task, config, prompt, console, status, verbose)
                    except AgentRunError as e:
                        task.status = TaskStatus.FAILED
                        write_plan(plan, plan_filepath)
                        status.stop()
                        _print_llm_error(console, task, total, e)
                        status.start()
                        stopped = True
                        break
                    total_usage += retry_result.usage
                    if retry_result.success:
                        task.status = TaskStatus.DONE
                        rebuilt_specs.add(task.spec)
                        rebuilt_tasks.add(task.id)
                        _store_task_state(
                            task, prompt, retry_result.created_files, retry_result.edited_files
                        )
                        console.log(
                            f"  [green]v [{task.id}/{total:03d}] {task.title} "
                            f"(retry)[/green]  [dim]({retry_result.summary()})[/dim]"
                        )
                    else:
                        task.status = TaskStatus.FAILED
                        console.print()
                        console.print(
                            Panel(
                                f"Task [bold]{task.id}[/bold] still failing.\n"
                                f"Review: [cyan].ossature/tasks/{task.id}-*/[/cyan]\n"
                                f"Resume: [cyan]ossature build[/cyan]",
                                title="[bold red]Build Stopped[/bold red]",
                                border_style="red",
                                expand=False,
                                box=box.ROUNDED,
                            )
                        )
                        stopped = True
                    write_plan(plan, plan_filepath)
                    if retry_result.success:
                        _maybe_extract_interface(task, status)
                    if stopped:
                        break
                elif action == "skip":
                    task.status = TaskStatus.SKIPPED
                    write_plan(plan, plan_filepath)
                    console.log(
                        f"  [dim][{task.id}/{total:03d}] {task.title} (skipped by user)[/dim]"
                    )
                else:
                    console.print()
                    console.print(
                        Panel(
                            f"Task [bold]{task.id}[/bold] failed after "
                            f"{config.build.max_fix_attempts} fix attempts.\n"
                            f"Review: [cyan].ossature/tasks/{task.id}-*/[/cyan]\n"
                            f"Resume: [cyan]ossature build[/cyan]",
                            title="[bold red]Build Stopped[/bold red]",
                            border_style="red",
                            expand=False,
                            box=box.ROUNDED,
                        )
                    )
                    stopped = True
                    break

    if stopped:
        return

    # Final summary
    done = sum(1 for t in plan.tasks if t.status == TaskStatus.DONE)
    built_this_run = done - completed_before
    failed = sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)
    skipped = sum(1 for t in plan.tasks if t.status == TaskStatus.SKIPPED)

    summary = Text()
    summary.append(f"  Done: {done}/{total}", style="bold green")
    if built_this_run > 0:
        summary.append(f"  (built {built_this_run} this run)", style="dim")
    if failed:
        summary.append(f"  Failed: {failed}", style="bold red")
    if skipped:
        summary.append(f"  Skipped: {skipped}", style="dim")
    if total_usage.requests > 0:
        summary.append(f"  LLM: {total_usage.format_usage()}", style="dim")

    console.print()
    console.print(
        Panel(
            summary,
            title=f"[bold]{config.name} v{config.version} — Build Complete[/bold]",
            expand=False,
            box=box.ROUNDED,
        )
    )
