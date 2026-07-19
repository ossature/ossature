"""Single-task build: implement, verify, fix loop, review."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import tomli_w
from pydantic_ai.exceptions import AgentRunError
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.status import Status

from ossature.build.agents import (
    _create_fix_agent,
    _create_impl_agent,
    _create_review_agent,
    _run_with_retry,
)
from ossature.build.commands import (
    _format_verify_for_display,
    _truncate_output,
    is_verify_command_error,
    run_verify,
)
from ossature.build.prompts import (
    _task_is_reviewable,
    assemble_fix_prompt,
    assemble_review_fix_prompt,
    assemble_review_prompt,
)
from ossature.build.state import make_task_slug
from ossature.build.tools import BuildContext
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.plan import PlanTask
from ossature.models.review import ReviewReport
from ossature.models.smd import SMDSpec
from ossature.shared.llm import UsageTracker, run_agent_sync

_MAX_NOOP_RETRIES: int = 2


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


def _print_verify_errors(console: Console, verify_output: str) -> None:
    # Compiler and test output can contain [...] sequences rich would
    # misread as markup
    truncated = escape(_truncate_output(verify_output))
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
    truncated = escape(_truncate_output(verify_output))
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


def _format_review_issues(report: ReviewReport) -> str:
    return "\n".join(
        f"{i.file} -- {i.target}: {i.problem} (fix: {i.suggestion})" for i in report.issues
    )


def _print_review_errors(console: Console, task: PlanTask, report: ReviewReport) -> None:
    lines = [
        f"Review found problems in task [bold]{task.id}[/bold] that the fix "
        "attempts did not resolve:\n"
    ]
    for issue in report.issues:
        lines.append(f"[red]x[/red] {escape(issue.file)} -- {escape(issue.target)}")
        lines.append(f"   {escape(issue.problem)}")
        if issue.suggestion:
            lines.append(f"   [dim]fix: {escape(issue.suggestion)}[/dim]")
    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold red]Review Failed[/bold red]",
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

    def review(self, prompt: str, tracker: UsageTracker, model_name: str) -> ReviewReport: ...


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

    def review(self, prompt: str, tracker: UsageTracker, model_name: str) -> ReviewReport:
        agent = _create_review_agent(self._config)
        result = run_agent_sync(
            agent, prompt, operation="review", model_name=model_name, tracker=tracker
        )
        output: ReviewReport = result.output
        return output


def build_task(
    task: PlanTask,
    config: OssatureConfig,
    prompt: str,
    console: Console,
    status: Status,
    verbose: bool = False,
    *,
    backend: BuildBackend | None = None,
    smd_map: dict[str, SMDSpec] | None = None,
    amd_by_spec: dict[str, list[AMDSpec]] | None = None,
    final_outputs: list[str] | None = None,
) -> TaskResult:
    backend = backend or DefaultBuildBackend(config)
    smd_map = smd_map or {}
    amd_by_spec = amd_by_spec or {}
    # Component contracts are checked only against the files this task finalizes;
    # a file a later task rewrites is not this task's to satisfy.
    contract_paths = task.outputs if final_outputs is None else final_outputs

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

    verify_label = _format_verify_for_display(task.verify)

    def _review_and_finish(verify_output: str) -> TaskResult:
        # Second gate after verify: an LLM reviewer checks the generated code
        # against the task's spec requirements and declared contracts. Runs
        # only when review is enabled and the task has something to check, and
        # only when a task actually builds, so cached tasks are not re-reviewed.
        amds = amd_by_spec.get(task.spec)
        if not config.build.review or not _task_is_reviewable(task, amds, contract_paths):
            save_task_output(
                task_dir, build_ctx.created_files, build_ctx.edited_files, True, verify_output
            )
            return _make_result(True)

        review_model = config.llm.model_for("reviewer")
        review_round = 0

        def _review() -> ReviewReport | None:
            nonlocal review_round
            review_round += 1
            review_prompt = assemble_review_prompt(
                task, config, smd_map, amd_by_spec, contract_paths
            )
            (task_dir / f"review-{review_round}-prompt.md").write_text(review_prompt)
            try:
                report = backend.review(review_prompt, tracker=task_usage, model_name=review_model)
            except AgentRunError as e:
                console.log(
                    f"    [yellow]Reviewer error: {e.message} -- accepting the task[/yellow]"
                )
                (task_dir / f"review-{review_round}-response.md").write_text(
                    f"[reviewer error] {e.message}"
                )
                return None
            (task_dir / f"review-{review_round}-response.json").write_text(
                report.model_dump_json(indent=2)
            )
            return report

        build_ctx.set_phase("-- reviewing")
        report = _review()
        review_attempt = 0
        while report is not None and not report.passed:
            if review_attempt >= config.build.max_review_attempts:
                _print_review_errors(console, task, report)
                save_task_output(
                    task_dir,
                    build_ctx.created_files,
                    build_ctx.edited_files,
                    False,
                    _format_review_issues(report),
                )
                return _make_result(False)
            review_attempt += 1
            build_ctx.set_phase(
                f"-- fixing review ({review_attempt}/{config.build.max_review_attempts})"
            )
            rfix_prompt = assemble_review_fix_prompt(
                task, report, config, smd_map, amd_by_spec, contract_paths
            )
            (task_dir / f"review-fix-{review_attempt}-prompt.md").write_text(rfix_prompt)
            try:
                rfix_output = backend.fix(
                    rfix_prompt, build_ctx, console, tracker=task_usage, model_name=build_model
                )
            except AgentRunError as e:
                console.log(
                    f"    [yellow]Review-fix agent error on attempt "
                    f"{review_attempt}: {e.message}[/yellow]"
                )
                continue
            (task_dir / f"review-fix-{review_attempt}-response.md").write_text(rfix_output)
            if task.verify:
                build_ctx.set_phase(f"-- re-verifying ({verify_label})")
                passed_v, verify_output = backend.verify(task.verify, config.output_path)
                if not passed_v:
                    _print_verify_errors(console, verify_output)
                    save_task_output(
                        task_dir,
                        build_ctx.created_files,
                        build_ctx.edited_files,
                        False,
                        verify_output,
                    )
                    return _make_result(False)
            build_ctx.set_phase("-- re-reviewing")
            report = _review()

        save_task_output(
            task_dir, build_ctx.created_files, build_ctx.edited_files, True, verify_output
        )
        return _make_result(True)

    if not task.verify:
        return _review_and_finish("")

    # Verification
    build_ctx.set_phase(f"-- verifying ({verify_label})")
    passed, verify_output = backend.verify(task.verify, config.output_path)

    if passed:
        return _review_and_finish(verify_output)

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

    # Fix loop — fresh agent per attempt to avoid accumulating fix history.
    # The prompt is reassembled only when re-verification produces new
    # output, so a noop-retry reminder appended below survives the retry.
    noop_count = 0
    attempt = 0
    base_fix_prompt = assemble_fix_prompt(task, verify_output, config, verify_label)
    fix_prompt = base_fix_prompt
    while attempt < config.build.max_fix_attempts:
        build_ctx.set_phase(f"-- fixing ({attempt + 1}/{config.build.max_fix_attempts})")
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
                    base_fix_prompt + "\n\n<important>\n"
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
            return _review_and_finish(verify_output)
        attempt += 1
        base_fix_prompt = assemble_fix_prompt(task, verify_output, config, verify_label)
        fix_prompt = base_fix_prompt

    # Only show errors after all fix attempts exhausted
    _print_verify_errors(console, verify_output)
    save_task_output(
        task_dir, build_ctx.created_files, build_ctx.edited_files, False, verify_output
    )
    return _make_result(False)
