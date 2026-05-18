from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.status import Status

from ossature.build.state import make_task_slug
from ossature.config.loader import OssatureConfig
from ossature.models.plan import PlanTask
from ossature.shared.llm import UsageTracker

if TYPE_CHECKING:
    from ossature.build.builder import TaskResult


class CopyTaskError(Exception):
    """Raised when a copy task cannot be executed."""


def _classify_pattern(pattern: str) -> tuple[str, str, str] | None:
    """Split a pattern into (prefix, wildcard, suffix).

    Returns None for a literal pattern with no wildcard. Raises CopyTaskError
    if the pattern contains more than one wildcard (`*` or `**`); v1 supports
    at most one per pattern.
    """
    if "**" in pattern:
        before, _, after = pattern.partition("**")
        if "**" in after or "*" in before or "*" in after:
            raise CopyTaskError(
                f"pattern {pattern!r} has multiple wildcards; "
                f"v1 supports at most one '*' or '**' per pattern"
            )
        return (before, "**", after)
    if "*" in pattern:
        before, _, after = pattern.partition("*")
        if "*" in after:
            raise CopyTaskError(
                f"pattern {pattern!r} has multiple wildcards; "
                f"v1 supports at most one '*' or '**' per pattern"
            )
        return (before, "*", after)
    return None


def resolve_source_matches(source: list[str], context_dir: Path) -> list[list[str]]:
    """For each source pattern, return its sorted matched files relative to context_dir.

    Pure function: no I/O beyond filesystem glob. Returns an empty inner list
    for patterns with no matches; the caller decides whether that is an error.
    """
    if not context_dir.is_dir():
        return [[] for _ in source]
    ctx_resolved = context_dir.resolve()
    result: list[list[str]] = []
    for pattern in source:
        matched: list[str] = []
        for p in sorted(ctx_resolved.glob(pattern)):
            if not p.is_file():
                continue
            resolved = p.resolve()
            try:
                rel = resolved.relative_to(ctx_resolved)
            except ValueError:
                continue
            matched.append(str(rel))
        result.append(matched)
    return result


def map_sources_to_outputs(
    source: list[str],
    matches: list[list[str]],
    outputs: list[str],
) -> list[tuple[str, str]]:
    """Pair each matched source file with its destination output path.

    Pairs source[i] with outputs[i] (1:1 by index). For each pair, either both
    are literals (single file copy) or both share a wildcard slot (basename or
    path-suffix substitution). Raises CopyTaskError on any ambiguity, empty
    match set, or count mismatch.
    """
    if len(source) != len(outputs):
        raise CopyTaskError(
            f"source has {len(source)} entr(ies) but outputs has {len(outputs)}; "
            f"each source pattern must pair 1:1 with an output pattern"
        )

    pairs: list[tuple[str, str]] = []
    for src_pattern, src_matches, out_pattern in zip(source, matches, outputs, strict=True):
        if not src_matches:
            raise CopyTaskError(f"source {src_pattern!r} matched no files in the context directory")

        src_wildcard = _classify_pattern(src_pattern)
        out_wildcard = _classify_pattern(out_pattern)

        if src_wildcard is None:
            if len(src_matches) != 1:
                raise CopyTaskError(
                    f"literal source {src_pattern!r} resolved to {len(src_matches)} files"
                )
            if out_wildcard is not None:
                raise CopyTaskError(
                    f"source {src_pattern!r} is literal but paired output "
                    f"{out_pattern!r} has a wildcard"
                )
            pairs.append((src_matches[0], out_pattern))
            continue

        if out_wildcard is None:
            raise CopyTaskError(
                f"source {src_pattern!r} has a wildcard but paired output "
                f"{out_pattern!r} does not; add a matching wildcard to the output"
            )

        src_prefix, _, src_suffix = src_wildcard
        out_prefix, _, out_suffix = out_wildcard
        for matched in src_matches:
            if not matched.startswith(src_prefix):
                raise CopyTaskError(
                    f"matched file {matched!r} does not fit source pattern {src_pattern!r}"
                )
            if src_suffix and not matched.endswith(src_suffix):
                raise CopyTaskError(
                    f"matched file {matched!r} does not fit source pattern {src_pattern!r}"
                )
            captured_end = len(matched) - len(src_suffix) if src_suffix else len(matched)
            captured = matched[len(src_prefix) : captured_end]
            dest = f"{out_prefix}{captured}{out_suffix}"
            pairs.append((matched, dest))

    return pairs


def assemble_copy_task_prompt(task: PlanTask, config: OssatureConfig) -> str:
    """Build a deterministic synthetic prompt for a copy task.

    Used as the input-hash seed in place of the LLM prompt. Includes the source
    patterns, the outputs, and the currently-matched files so the input hash
    changes when any of these change.
    """
    lines: list[str] = []
    lines.append("<copy_task>")
    lines.append(f"id: {task.id}")
    lines.append(f"title: {task.title}")
    lines.append("source:")
    for s in task.source:
        lines.append(f"- context://{s}")
    lines.append("outputs:")
    for o in task.outputs:
        lines.append(f"- {o}")
    matches = resolve_source_matches(task.source, config.context_path)
    flat = sorted({m for sub in matches for m in sub})
    lines.append("matched_sources:")
    for m in flat:
        lines.append(f"- {m}")
    lines.append("</copy_task>")
    return "\n".join(lines)


def build_copy_task(
    task: PlanTask,
    config: OssatureConfig,
    console: Console,
    status: Status,
    verbose: bool = False,
) -> TaskResult:
    """Execute a copy-only task: copy files from context to output, no LLM call."""
    from ossature.build.builder import TaskResult, save_task_output

    slug = make_task_slug(task)
    task_dir = config.metadata_path / "tasks" / f"{task.id}-{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)

    task_label = f"[{task.id}] {task.title}"
    status.update(f"{task_label} -- copying...")

    prompt = assemble_copy_task_prompt(task, config)
    (task_dir / "prompt.md").write_text(prompt)

    t0 = time.monotonic()

    def _fail(message: str, created_so_far: list[str]) -> TaskResult:
        (task_dir / "response.md").write_text(f"[copy task error] {message}\n")
        save_task_output(task_dir, created_so_far, [], False, message)
        return TaskResult(
            success=False,
            file_count=len(created_so_far),
            total_lines=0,
            elapsed=time.monotonic() - t0,
            created_files=created_so_far,
            edited_files=[],
            usage=UsageTracker(),
        )

    if not task.source:
        return _fail(f"copy task {task.id} has no source patterns", [])

    if not config.context_path.is_dir():
        return _fail(
            f"copy task {task.id} requires context directory "
            f"{config.context_path} but it does not exist",
            [],
        )

    try:
        matches = resolve_source_matches(task.source, config.context_path)
        pairs = map_sources_to_outputs(task.source, matches, task.outputs)
    except CopyTaskError as e:
        return _fail(str(e), [])

    created_files: list[str] = []
    output_dir_resolved = config.output_path.resolve()
    context_dir_resolved = config.context_path.resolve()

    for src_rel, dst_rel in pairs:
        src_full = (context_dir_resolved / src_rel).resolve()
        if not src_full.is_relative_to(context_dir_resolved):
            return _fail(
                f"source {src_rel!r} resolves outside the context directory",
                created_files,
            )
        if not src_full.exists() or not src_full.is_file():
            return _fail(f"source file {src_rel!r} does not exist", created_files)

        dst_full = (output_dir_resolved / dst_rel).resolve()
        if not dst_full.is_relative_to(output_dir_resolved):
            return _fail(
                f"output {dst_rel!r} resolves outside the output directory",
                created_files,
            )

        status.update(f"{task_label} -- copying {src_rel} -> {dst_rel}")
        try:
            dst_full.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_full), str(dst_full))
        except OSError as e:
            return _fail(f"failed copying {src_rel!r} to {dst_rel!r}: {e}", created_files)

        if dst_rel not in created_files:
            created_files.append(dst_rel)
        if verbose:
            console.log(f"      copied [bold]{src_rel}[/bold] -> [bold]{dst_rel}[/bold]")

    summary_line = f"Copied {len(created_files)} file(s) from context to output."
    response_body = summary_line + "\n\n" + "\n".join(f"- {s} -> {d}" for s, d in pairs) + "\n"
    (task_dir / "response.md").write_text(response_body)
    save_task_output(task_dir, created_files, [], True, summary_line)

    return TaskResult(
        success=True,
        file_count=len(created_files),
        total_lines=0,
        elapsed=time.monotonic() - t0,
        created_files=created_files,
        edited_files=[],
        usage=UsageTracker(),
    )
