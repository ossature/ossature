import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from rich.console import Console
from rich.status import Status

from ossature.audit.prompts import SPEC_FIXER_SYSTEM_PROMPT
from ossature.config.loader import OssatureConfig
from ossature.models.audit import AuditFinding, CrossSpecFinding, Severity
from ossature.shared import FileEdit, apply_edits


def _format_fix_error(e: Exception) -> str:
    """Extract a useful error message, including the root cause from retries."""
    if isinstance(e, UnexpectedModelBehavior) and e.__cause__:
        return f"{e.message}: {e.__cause__}"
    return str(e)


# Process errors first, then warnings, then info
_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


@dataclass
class FixContext:
    spec_dir: Path
    console: Console
    status: Status
    edited_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.spec_dir = self.spec_dir.resolve()


def _resolve_spec_sandboxed(spec_dir: Path, path: str) -> Path:
    resolved = (spec_dir / path).resolve()
    if not resolved.is_relative_to(spec_dir):
        raise ModelRetry(
            f"Access denied: '{path}' resolves outside the spec directory. "
            f"Use a relative path within the spec directory."
        )
    return resolved


def _register_fixer_tools(agent: Agent[FixContext, str]) -> None:
    @agent.tool
    def read_file(ctx: RunContext[FixContext], path: str) -> str:
        full_path = _resolve_spec_sandboxed(ctx.deps.spec_dir, path)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.status.update(f"fixing -- reading {path}")
            return full_path.read_text()
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def grep_file(ctx: RunContext[FixContext], path: str, pattern: str) -> str:
        full_path = _resolve_spec_sandboxed(ctx.deps.spec_dir, path)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.status.update(f"fixing -- searching {path}")
            lines = full_path.read_text().splitlines()
            compiled = re.compile(pattern, re.IGNORECASE)
            matches: list[str] = []
            for i, line in enumerate(lines):
                if compiled.search(line):
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
    def edit_file(ctx: RunContext[FixContext], path: str, edits: list[FileEdit]) -> str:
        full_path = _resolve_spec_sandboxed(ctx.deps.spec_dir, path)
        try:
            if not full_path.exists():
                raise ModelRetry(f"Cannot edit '{path}': file does not exist.")
            content = full_path.read_text()
        except OSError as e:
            return f"Error reading {path}: {e}"

        updated = apply_edits(content, edits)
        try:
            full_path.write_text(updated)
        except OSError as e:
            return f"Error writing {path}: {e}"

        if path not in ctx.deps.edited_files:
            ctx.deps.edited_files.append(path)

        ctx.deps.status.update(f"fixing -- edited {path}")
        ctx.deps.console.log(f"    edited [bold]{path}[/bold] ({len(edits)} edit(s))")
        return f"Edited: {path} ({len(edits)} edit(s) applied)"


def _create_fixer_agent(config: OssatureConfig) -> Agent[FixContext, str]:
    agent: Agent[FixContext, str] = Agent(
        config.llm.model_for("fixer"),
        system_prompt=SPEC_FIXER_SYSTEM_PROMPT,
        deps_type=FixContext,
        retries=config.llm.tool_retries,
        model_settings={"max_tokens": 8192},
    )
    _register_fixer_tools(agent)
    return agent


def _build_finding_prompt(
    finding: AuditFinding,
    spec_file: str,
) -> str:
    return (
        f"<finding>\n"
        f"**Severity:** {finding.severity.value.upper()}\n"
        f"**Location:** {finding.location}\n"
        f"**Issue:** {finding.issue}\n"
        f"**Suggestion:** {finding.suggestion}\n"
        f"</finding>\n\n"
        f"<target_file>{spec_file}</target_file>\n\n"
        f"Read the file, find the relevant section, and make the minimal edit "
        f"to address this finding."
    )


def _build_cross_spec_finding_prompt(
    finding: CrossSpecFinding,
    spec_files: dict[str, str],
) -> str:
    files_section = "\n".join(
        f"- {spec_id}: `{filepath}`" for spec_id, filepath in spec_files.items()
    )
    return (
        f"<finding>\n"
        f"**Severity:** {finding.severity.value.upper()}\n"
        f"**Specs involved:** {', '.join(finding.specs)}\n"
        f"**Issue:** {finding.issue}\n"
        f"**Suggestion:** {finding.suggestion}\n"
        f"</finding>\n\n"
        f"<spec_files>\n{files_section}\n</spec_files>\n\n"
        f"Read the relevant spec file(s), find the sections that need changes, "
        f"and make the minimal edits to address this finding. "
        f"You may need to edit one or more of the listed files."
    )


def fix_spec_findings(
    findings: list[AuditFinding],
    spec_file: str,
    spec_dir: Path,
    config: OssatureConfig,
    console: Console,
    status: Status,
) -> list[str]:
    agent = _create_fixer_agent(config)
    all_edited: list[str] = []

    has_errors_or_warnings = any(f.severity in (Severity.ERROR, Severity.WARNING) for f in findings)
    sorted_findings = sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))

    for finding in sorted_findings:
        if finding.severity == Severity.INFO and has_errors_or_warnings:
            continue
        prompt = _build_finding_prompt(finding, spec_file)

        # Save backup before each fix attempt
        full_path = (spec_dir / spec_file).resolve()
        backup = full_path.read_text()

        fix_ctx = FixContext(
            spec_dir=spec_dir,
            console=console,
            status=status,
        )

        try:
            agent.run_sync(prompt, deps=fix_ctx)

            # Verify file still parses after edit
            if not _verify_spec_parses(full_path):
                console.log("    [red]Fix broke file parsing — reverting[/red]")
                full_path.write_text(backup)
                continue

            for f in fix_ctx.edited_files:
                if f not in all_edited:
                    all_edited.append(f)

        except Exception as e:
            console.log(f"    [red]Fix failed: {_format_fix_error(e)} — skipping[/red]")
            full_path.write_text(backup)

    return all_edited


def fix_cross_spec_findings(
    findings: list[CrossSpecFinding],
    spec_files: dict[str, str],
    spec_dir: Path,
    config: OssatureConfig,
    console: Console,
    status: Status,
) -> list[str]:
    agent = _create_fixer_agent(config)
    all_edited: list[str] = []

    has_errors_or_warnings = any(f.severity in (Severity.ERROR, Severity.WARNING) for f in findings)
    sorted_findings = sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))

    for finding in sorted_findings:
        if finding.severity == Severity.INFO and has_errors_or_warnings:
            continue

        # Only include files for specs mentioned in this finding
        relevant_files = {sid: spec_files[sid] for sid in finding.specs if sid in spec_files}
        if not relevant_files:
            continue

        prompt = _build_cross_spec_finding_prompt(finding, relevant_files)

        # Save backups for all relevant files
        backups: dict[Path, str] = {}
        for filepath in relevant_files.values():
            full_path = (spec_dir / filepath).resolve()
            if full_path.exists():
                backups[full_path] = full_path.read_text()

        fix_ctx = FixContext(
            spec_dir=spec_dir,
            console=console,
            status=status,
        )

        try:
            agent.run_sync(prompt, deps=fix_ctx)

            # Verify all edited files still parse
            revert = False
            for filepath in fix_ctx.edited_files:
                full_path = (spec_dir / filepath).resolve()
                if not _verify_spec_parses(full_path):
                    console.log(f"    [red]Fix broke {filepath} parsing — reverting all[/red]")
                    revert = True
                    break

            if revert:
                for full_path, content in backups.items():
                    full_path.write_text(content)
                continue

            for f in fix_ctx.edited_files:
                if f not in all_edited:
                    all_edited.append(f)

        except Exception as e:
            console.log(f"    [red]Fix failed: {_format_fix_error(e)} — skipping[/red]")
            for full_path, content in backups.items():
                full_path.write_text(content)

    return all_edited


def _verify_spec_parses(path: Path) -> bool:
    """Check that a spec file still parses after editing."""
    from ossature.parsers.amd import AMDParseError, parse_amd_file
    from ossature.parsers.smd import SMDParseError, parse_smd_file

    suffix = path.name
    try:
        if suffix.endswith(".amd"):
            parse_amd_file(path)
        else:
            parse_smd_file(path)
        return True
    except SMDParseError, AMDParseError:
        return False
