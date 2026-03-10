import json
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import content_types
import tomli_w
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from ossature.audit.planner import write_plan
from ossature.build.prompts import (
    FIXER_SYSTEM_PROMPT,
    IMPLEMENTER_SYSTEM_PROMPT,
    INTERFACE_EXTRACTION_SYSTEM_PROMPT,
)
from ossature.build.state import (
    TaskState,
    compute_input_hash,
    compute_output_hash,
    get_task_written_files,
    load_state,
    write_state,
)
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.plan import Plan, PlanTask, TaskStatus
from ossature.models.smd import SMDSpec
from ossature.renderer.amd import render_component, render_data_model, render_dependency
from ossature.renderer.smd import render_example, render_requirement
from ossature.shared import apply_edits


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
    written_files: list[str] = field(default_factory=list)
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


_COMMAND_DENY_PATTERNS = re.compile(
    r"""
      \.\./              # directory traversal
    | /\.\.(?:/|$)       # traversal after absolute prefix
    | (?:^|[\s;&|])\s*/  # absolute path at start, after whitespace, or after shell separator
    """,
    re.VERBOSE,
)


def _validate_command(command: str, console: Console) -> None:
    if _COMMAND_DENY_PATTERNS.search(command):
        console.log(f"    [red] Command denied:[/red] [bold]{command}[/bold]")
        raise ModelRetry(
            f"Access denied: command '{command}' contains path traversal or absolute paths. "
            f"All commands run inside the output directory. "
            f"Use relative paths only — no '..' or '/'."
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
        is_new = path not in ctx.deps.written_files
        if is_new:
            ctx.deps.written_files.append(path)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        ctx.deps.total_lines += line_count
        action = "wrote" if is_new else "updated"
        ctx.deps.set_phase(f"-- {action} {path}")
        ctx.deps.log_tool(f"      {action} [bold]{path}[/bold] ({line_count} lines)")
        return f"Written: {path} ({len(content)} bytes, {line_count} lines)"

    @agent.tool
    def edit_file(ctx: RunContext[BuildContext], path: str, edits: str) -> str:
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

        if path not in ctx.deps.written_files:
            ctx.deps.written_files.append(path)

        n_edits = len(json.loads(edits))
        ctx.deps.set_phase(f"-- edited {path}")
        ctx.deps.log_tool(f"      edited [bold]{path}[/bold] ({n_edits} edit(s))")
        return f"Edited: {path} ({n_edits} edit(s) applied)"

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
        _validate_command(command, ctx.deps.console)
        ctx.deps.set_phase(f"-- running: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
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
        if dest_path not in ctx.deps.written_files:
            ctx.deps.written_files.append(dest_path)
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
        system_prompt=IMPLEMENTER_SYSTEM_PROMPT.format(language=config.output.language),
        deps_type=BuildContext,
        retries=3,
        model_settings={"max_tokens": 16384},
    )
    _register_tools(agent)
    return agent


def _create_fix_agent(config: OssatureConfig) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        config.llm.model_for("build"),
        system_prompt=FIXER_SYSTEM_PROMPT.format(language=config.output.language),
        deps_type=BuildContext,
        retries=3,
        model_settings={"max_tokens": 16384},
    )
    _register_tools(agent)
    return agent


# Rate-limit retry


def _run_with_retry(
    agent: Agent[BuildContext, str],
    prompt: str,
    deps: BuildContext,
    console: Console,
    max_retries: int = 5,
    base_delay: float = 30.0,
) -> Any:
    for attempt in range(max_retries):
        try:
            return agent.run_sync(prompt, deps=deps, usage_limits=UsageLimits(request_limit=200))
        except ModelHTTPError as e:
            if e.status_code != 429 or attempt >= max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            console.log(
                f"    [yellow] Rate limited, retrying in {delay:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})[/yellow]"
            )
            time.sleep(delay)
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
            _, _, ref_section = ref.partition(":")
            if not ref_section:
                continue
            rendered = _render_spec_ref(smd, ref_section.strip())
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
            _, _, ref_section = ref.partition(":")
            if not ref_section:
                continue
            rendered = _render_arch_ref(amds, ref_section.strip())
            if rendered:
                arch_parts.append(rendered)
        if arch_parts:
            sections.append(
                "<architecture_context>\n" + "\n\n".join(arch_parts) + "\n</architecture_context>"
            )

    # Inject files — list available dependency files for tool-based exploration
    if task.inject_files:
        available: list[str] = []
        for filepath in task.inject_files:
            full_path = config.output_path / filepath
            if full_path.exists():
                mime_type = content_types.get_content_type(full_path.name)
                is_text = mime_type.startswith("text/") or mime_type in {
                    "application/json",
                    "application/xml",
                    "application/toml",
                    "application/yaml",
                }
                if is_text:
                    try:
                        line_count = len(full_path.read_text().splitlines())
                        available.append(f"- `{filepath}` ({line_count} lines)")
                    except UnicodeDecodeError, ValueError:
                        size = full_path.stat().st_size
                        available.append(f"- `{filepath}` (`{mime_type}`, {size} bytes, binary)")
                else:
                    size = full_path.stat().st_size
                    available.append(f"- `{filepath}` (`{mime_type}`, {size} bytes, binary)")
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

    # Include output files, falling back to inject_files for modify-in-place tasks
    file_list = task.outputs if task.outputs else (task.inject_files or [])
    for filepath in file_list:
        full_path = config.output_path / filepath
        if full_path.exists():
            try:
                content = full_path.read_text()
                sections.append(
                    f'<current_file path="{filepath}">\n```\n{content}\n```\n</current_file>'
                )
            except UnicodeDecodeError:
                pass

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
    has_source_ref = any(output_str in ln or "Error:" in ln and "/" in ln for ln in error_lines)

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


def run_verify(command: str, cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "Verify command timed out after 120 seconds"
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        if output:
            output += "\n"
        output += result.stderr
    return result.returncode == 0, output.strip()


# Task building


def make_task_slug(task: PlanTask) -> str:
    slug = task.title.lower().replace(" ", "-").replace(":", "").replace("/", "-")
    return slug.strip("-")


def save_task_output(
    task_dir: Path, written_files: list[str], success: bool, verify_output: str
) -> None:
    data: dict[str, Any] = {
        "files": written_files,
        "success": success,
        "verify_output": verify_output,
    }
    with open(task_dir / "output.toml", "wb") as f:
        tomli_w.dump(data, f)


def extract_spec_interface(
    spec_id: str,
    plan: Plan,
    config: OssatureConfig,
    console: Console,
    status: Status,
) -> None:
    source_files: list[tuple[str, str]] = []
    for task in plan.tasks:
        if task.spec == spec_id and task.status == TaskStatus.DONE:
            for filepath in task.outputs:
                full_path = config.output_path / filepath
                if full_path.exists():
                    try:
                        source_files.append((filepath, full_path.read_text()))
                    except OSError:
                        pass

    if not source_files:
        return

    language = config.output.language
    sections = [f"# Source files for {spec_id}\n"]
    for filepath, content in source_files:
        sections.append(f"## {filepath}\n\n```{language}\n{content}\n```\n")

    status.update(f"Extracting interface: {spec_id}")
    console.log(f"  [cyan]Extracting interface for {spec_id}...[/cyan]")

    agent = Agent(
        config.llm.model_for("interface"),
        instructions=INTERFACE_EXTRACTION_SYSTEM_PROMPT.format(language=language),
    )
    result = agent.run_sync("\n".join(sections))

    interface_content = f"# Interface: {spec_id}\n\n@source: build\n\n{result.output}"

    iface_dir = config.metadata_context_interfaces_path
    iface_dir.mkdir(parents=True, exist_ok=True)
    (iface_dir / f"{spec_id}.md").write_text(interface_content)

    console.log(f"  [green]Interface written: .ossature/context/interfaces/{spec_id}.md[/green]")


def _truncate_output(text: str, max_lines: int = 30) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = lines[:10] + [f"  ... ({len(lines) - 20} lines omitted) ..."] + lines[-10:]
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


def _print_verify_command_error(console: Console, task: PlanTask, verify_output: str) -> None:
    truncated = _truncate_output(verify_output)
    body = (
        f"The verify command itself appears to be invalid — this is not a code error.\n\n"
        f"  Command: [bold]{task.verify}[/bold]\n\n"
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


@dataclass
class TaskResult:
    success: bool
    file_count: int = 0
    total_lines: int = 0
    elapsed: float = 0.0
    written_files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.file_count:
            files_word = "file" if self.file_count == 1 else "files"
            parts.append(f"{self.file_count} {files_word}")
        if self.total_lines:
            parts.append(f"{self.total_lines} lines")
        parts.append(f"{self.elapsed:.1f}s")
        return ", ".join(parts)


def build_task(
    task: PlanTask,
    config: OssatureConfig,
    prompt: str,
    console: Console,
    status: Status,
    verbose: bool = False,
) -> TaskResult:
    impl_agent = _create_impl_agent(config)

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

    # Implementation
    build_ctx.set_phase("-- generating...")
    result = _run_with_retry(impl_agent, prompt, build_ctx, console)
    (task_dir / "response.md").write_text(result.output)

    def _make_result(success: bool) -> TaskResult:
        return TaskResult(
            success=success,
            file_count=len(build_ctx.written_files),
            total_lines=build_ctx.total_lines,
            elapsed=time.monotonic() - t0,
            written_files=list(build_ctx.written_files),
        )

    if not task.verify:
        save_task_output(task_dir, build_ctx.written_files, True, "")
        return _make_result(True)

    # Verification
    build_ctx.set_phase(f"-- verifying ({task.verify})")
    passed, verify_output = run_verify(task.verify, config.output_path)

    if passed:
        save_task_output(task_dir, build_ctx.written_files, True, verify_output)
        return _make_result(True)

    # Check if the error is a command invocation problem, not a code problem
    if is_verify_command_error(verify_output, config.output_path):
        _print_verify_command_error(console, task, verify_output)
        save_task_output(task_dir, build_ctx.written_files, False, verify_output)
        return _make_result(False)

    # Fix loop — fresh agent per attempt to avoid accumulating fix history
    for attempt in range(config.build.max_fix_attempts):
        build_ctx.set_phase(f"-- fixing ({attempt + 1}/{config.build.max_fix_attempts})")
        fix_prompt = assemble_fix_prompt(task, verify_output, config, task.verify)
        (task_dir / f"fix-{attempt + 1}-prompt.md").write_text(fix_prompt)

        fix_agent = _create_fix_agent(config)
        fix_result = _run_with_retry(fix_agent, fix_prompt, build_ctx, console)
        (task_dir / f"fix-{attempt + 1}-response.md").write_text(fix_result.output)

        build_ctx.set_phase(f"-- re-verifying ({task.verify})")
        passed, verify_output = run_verify(task.verify, config.output_path)
        if passed:
            save_task_output(task_dir, build_ctx.written_files, True, verify_output)
            return _make_result(True)

    # Only show errors after all fix attempts exhausted
    _print_verify_errors(console, verify_output)
    save_task_output(task_dir, build_ctx.written_files, False, verify_output)
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

    console.print(f"  Running setup: [bold]{config.build.setup}[/bold]")
    try:
        result = subprocess.run(
            config.build.setup,
            shell=True,
            capture_output=True,
            text=True,
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


def _extract_commands_from_plan(plan: Plan, config: OssatureConfig) -> set[str]:
    commands: set[str] = set()

    if config.build.setup:
        commands.add(config.build.setup)
    if config.build.verify:
        commands.add(config.build.verify)
    if config.build.test:
        commands.add(config.build.test)

    for task in plan.tasks:
        if task.verify:
            commands.add(task.verify)

    return commands


def _extract_executables(commands: set[str]) -> set[str]:
    executables: set[str] = set()
    for cmd in commands:
        # Use shlex to split respecting quotes, then identify command
        # boundaries by looking for shell operators as standalone tokens.
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            # Malformed quoting — fall back to naive split on whitespace
            tokens = cmd.split()

        # Walk tokens, extracting the first word of each sub-command.
        # Shell operators (&&, ||, ;) delimit sub-commands.
        expect_command = True
        for token in tokens:
            if token in ("&&", "||", ";", "|"):
                expect_command = True
                continue
            if not expect_command:
                continue
            # Skip environment variable assignments (e.g., FOO=bar cmd)
            if "=" in token and not token.startswith("="):
                continue
            # Skip common shell builtins
            if token in ("cd", "echo", "export", "test", "[", "true", "false"):
                expect_command = False
                continue
            executables.add(token)
            expect_command = False
    return executables


def check_tool_availability(plan: Plan, config: OssatureConfig, console: Console) -> bool:
    commands = _extract_commands_from_plan(plan, config)
    if not commands:
        return True

    executables = _extract_executables(commands)
    if not executables:
        return True

    missing: list[tuple[str, list[str]]] = []
    for exe in sorted(executables):
        if not shutil.which(exe):
            # Find which commands reference this executable
            referencing = [cmd for cmd in commands if exe in cmd.split()]
            missing.append((exe, referencing))

    if not missing:
        return True

    console.print()
    console.print("[bold red]Missing required tools[/bold red]")
    console.print()
    for exe, referencing in missing:
        console.print(f"  [red]x[/red] [bold]{exe}[/bold] not found on PATH")
        for cmd in referencing:
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
    has_completed = any(t.status == TaskStatus.DONE for t in plan.tasks)
    if not has_completed and not run_setup(config, console):
        raise SystemExit(1)

    total = plan.meta.total_tasks
    completed_before = sum(1 for t in plan.tasks if t.status == TaskStatus.DONE)
    skip_next = False
    stopped = False

    # Load build state for input/output hash verification
    state_filepath = config.metadata_path / "state.toml"
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

    def _maybe_extract_interface(task: PlanTask, status: Status) -> None:
        if task.id != spec_last_task_id.get(task.spec):
            return
        if task.spec in extracted_interfaces and task.spec not in rebuilt_specs:
            return
        if not all(t.status == TaskStatus.DONE for t in tasks_by_spec[task.spec]):
            return
        try:
            extract_spec_interface(task.spec, plan, config, console, status)
        except AgentRunError as e:
            summary, _ = _describe_llm_error(e)
            console.log(
                f"  [yellow]Interface extraction failed for {task.spec}: {summary}[/yellow]"
            )
            return
        extracted_interfaces.add(task.spec)

    def _store_task_state(task: PlanTask, prompt: str, written_files: list[str]) -> None:
        input_h = compute_input_hash(prompt, task, config)
        output_h = compute_output_hash(written_files, config)
        state.set(task.id, TaskState(input_h, output_h, list(written_files)))
        write_state(state, state_filepath)

    with Status("", console=console) as status:
        for task in plan.tasks:
            if task.status == TaskStatus.SKIPPED:
                console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (skipped)[/dim]")
                continue

            if task.status == TaskStatus.DONE:
                prompt = assemble_task_prompt(task, config, smd_map, amd_by_spec)
                current_input_hash = compute_input_hash(prompt, task, config)
                stored = state.get(task.id)

                if stored and stored.input_hash == current_input_hash:
                    # Input unchanged — verify output integrity
                    current_output_hash = compute_output_hash(stored.written_files, config)
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
                    written_files = get_task_written_files(task, tasks_dir)
                    _store_task_state(task, prompt, written_files)
                    console.log(f"  [dim][{task.id}/{total:03d}] {task.title} (done)[/dim]")
                    _maybe_extract_interface(task, status)
                    continue

                # Stale — mark for re-run
                task.status = TaskStatus.PENDING
                write_plan(plan, plan_filepath)
                continue

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
            prompt = assemble_task_prompt(task, config, smd_map, amd_by_spec)

            # Run task with LLM error recovery
            llm_bail = False
            while True:
                try:
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

            if success:
                task.status = TaskStatus.DONE
                console.log(
                    f"  [green]v [{task.id}/{total:03d}] {task.title}[/green]"
                    f"  [dim]({result.summary()})[/dim]"
                )
                rebuilt_specs.add(task.spec)
                _store_task_state(task, prompt, result.written_files)
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
                    if retry_result.success:
                        task.status = TaskStatus.DONE
                        rebuilt_specs.add(task.spec)
                        _store_task_state(task, prompt, retry_result.written_files)
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

    console.print()
    console.print(
        Panel(
            summary,
            title=f"[bold]{config.name} v{config.version} — Build Complete[/bold]",
            expand=False,
            box=box.ROUNDED,
        )
    )
