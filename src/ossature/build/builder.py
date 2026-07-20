"""Build execution loop: task scheduling, caching, and interactive modes."""

from enum import Enum
from pathlib import Path

from pydantic_ai.exceptions import AgentRunError
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from ossature.audit.planner import write_plan
from ossature.build.agents import _describe_llm_error, _format_llm_error_body
from ossature.build.commands import check_tool_availability, run_setup
from ossature.build.copy import assemble_copy_task_prompt, build_copy_task
from ossature.build.interface import extract_spec_interface
from ossature.build.prompts import assemble_task_prompt, final_output_paths
from ossature.build.state import (
    TaskState,
    compute_input_hash,
    compute_output_hash,
    get_task_created_files,
    load_state,
    write_state,
)
from ossature.build.task import TaskResult, build_task
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.plan import Plan, PlanTask, TaskStatus
from ossature.models.smd import SMDSpec
from ossature.shared.llm import UsageTracker
from ossature.verification.build import assemble_verify_task_prompt, build_verify_task


class BuildMode(Enum):
    DEFAULT = "default"  # Pause on failure
    STEP = "step"  # Pause after every task
    AUTO = "auto"  # Run to completion, stop on failure
    AUTO_SKIP = "auto_skip"  # Run everything possible, skip failures


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


def _prompt_after_failure(console: Console) -> str:
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


def _run_task_dispatch(
    task: PlanTask,
    config: OssatureConfig,
    prompt: str,
    console: Console,
    status: Status,
    verbose: bool,
    plan: Plan,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
) -> TaskResult:
    """Run a task through the builder matching its kind (verify/copy/default)."""
    if task.kind == "verify":
        return build_verify_task(task, config, prompt, console, status, plan, amd_by_spec, verbose)
    if task.source:
        return build_copy_task(task, config, console, status, verbose)
    return build_task(
        task,
        config,
        prompt,
        console,
        status,
        verbose,
        smd_map=smd_map,
        amd_by_spec=amd_by_spec,
        final_outputs=final_output_paths(task, plan),
    )


class _BuildRun:
    """State for one execute_build invocation: the plan, the hash state, and
    the bookkeeping that decides which tasks re-run."""

    def __init__(
        self,
        config: OssatureConfig,
        plan: Plan,
        smd_map: dict[str, SMDSpec],
        amd_by_spec: dict[str, list[AMDSpec]],
        console: Console,
        plan_filepath: Path,
        mode: BuildMode,
        verbose: bool,
    ) -> None:
        self.config = config
        self.plan = plan
        self.smd_map = smd_map
        self.amd_by_spec = amd_by_spec
        self.console = console
        self.plan_filepath = plan_filepath
        self.mode = mode
        self.verbose = verbose

        self.state_filepath = config.metadata_path / "state.toml"
        self.state = load_state(self.state_filepath)
        self.tasks_dir = config.metadata_path / "tasks"
        self.total = plan.meta.total_tasks
        self.completed_before = sum(1 for t in plan.tasks if t.status == TaskStatus.DONE)
        self.total_usage = UsageTracker()
        self.skip_next = False
        self.stopped = False

        self.tasks_by_spec: dict[str, list[PlanTask]] = {}
        self.spec_last_task_id: dict[str, str] = {}
        self.spec_by_task_id: dict[str, str] = {}
        for t in plan.tasks:
            self.tasks_by_spec.setdefault(t.spec, []).append(t)
            self.spec_last_task_id[t.spec] = t.id
            self.spec_by_task_id[t.id] = t.spec

        self.extracted_interfaces: set[str] = {
            sid
            for sid in self.tasks_by_spec
            if (config.metadata_context_interfaces_path / f"{sid}.md").exists()
        }
        self.rebuilt_specs: set[str] = set()
        self.rebuilt_tasks: set[str] = set()
        # Specs whose interface file was successfully (re)extracted during this
        # run. A cross-spec dependent can rely on the input-hash gate only when
        # its upstream interface is fresh; if extraction failed or never ran,
        # the file on disk is stale and the dependent is forced to rebuild.
        self.interface_refreshed_specs: set[str] = set()

    def run(self) -> None:
        with Status("", console=self.console) as status:
            for task in self.plan.tasks:
                if not self._run_one(task, status):
                    break
        if self.stopped:
            return
        self._print_summary()

    def _save_plan(self) -> None:
        write_plan(self.plan, self.plan_filepath)

    def _assemble_prompt(self, task: PlanTask) -> str:
        if task.kind == "verify":
            return assemble_verify_task_prompt(task, self.config, self.plan, self.amd_by_spec)
        if task.source:
            return assemble_copy_task_prompt(task, self.config)
        return assemble_task_prompt(
            task,
            self.config,
            self.smd_map,
            self.amd_by_spec,
            final_outputs=final_output_paths(task, self.plan),
        )

    def _store_task_state(
        self,
        task: PlanTask,
        prompt: str,
        created_files: list[str],
        edited_files: list[str] | None = None,
    ) -> None:
        input_h = compute_input_hash(prompt, task, self.config)
        output_h = compute_output_hash(created_files, self.config)
        self.state.set(
            task.id, TaskState(input_h, output_h, list(created_files), list(edited_files or []))
        )
        write_state(self.state, self.state_filepath)

    def _maybe_extract_interface(self, task: PlanTask, status: Status) -> None:
        if task.id != self.spec_last_task_id.get(task.spec):
            return
        if task.spec in self.extracted_interfaces and task.spec not in self.rebuilt_specs:
            return
        if not all(t.status == TaskStatus.DONE for t in self.tasks_by_spec[task.spec]):
            return
        try:
            written = extract_spec_interface(
                task.spec,
                self.plan,
                self.config,
                self.console,
                status,
                tracker=self.total_usage,
                amds=self.amd_by_spec.get(task.spec),
            )
        except AgentRunError as e:
            summary, _ = _describe_llm_error(e)
            self.console.log(
                f"  [yellow]Interface extraction failed for {task.spec}: {summary}[/yellow]"
            )
            return
        # Only mark the spec refreshed when a file was actually written. An
        # upstream that rebuilt but had no extractable source leaves a stale (or
        # absent) interface file on disk, so it stays out of both sets and the
        # cross-spec staleness check forces its dependents to rebuild.
        if not written:
            return
        self.extracted_interfaces.add(task.spec)
        self.interface_refreshed_specs.add(task.spec)

    def _cached_task_stands(self, task: PlanTask, status: Status) -> bool:
        """Decide whether a DONE task can be skipped.

        True: inputs and outputs are unchanged, the task was skipped (hashes
        backfilled if missing). False: something is stale and the task was
        marked pending for a re-run."""
        prompt = self._assemble_prompt(task)
        current_input_hash = compute_input_hash(prompt, task, self.config)
        stored = self.state.get(task.id)

        # Same-spec dependencies travel as inject_files, which are
        # deliberately excluded from the input hash (see state.py), so a
        # same-spec rebuild must force this task to re-run. A dep id that
        # cannot be resolved to a spec defaults to same-spec, the
        # conservative direction that forces a rebuild rather than risk a
        # false skip.
        same_spec_rebuilt = any(
            d in self.rebuilt_tasks
            for d in task.depends_on
            if self.spec_by_task_id.get(d, task.spec) == task.spec
        )
        # Cross-spec dependencies travel as cross_spec_interfaces, which
        # are embedded in this task's prompt and therefore already covered
        # by the input-hash check below. Let a cross-spec edge fall
        # through to that check only when the upstream interface was
        # actually refreshed this run. If the upstream rebuilt but its
        # interface could not be re-extracted (extraction failed, no
        # extractable source, or its last task never reached DONE) the
        # interface file on disk is stale, the input-hash gate would read
        # stale bytes, so force a rebuild instead.
        cross_spec_stale = any(
            sid in self.rebuilt_specs and sid not in self.interface_refreshed_specs
            for sid in task.cross_spec_interfaces
        )

        if same_spec_rebuilt or cross_spec_stale:
            self.console.log(
                f"  [yellow][{task.id}/{self.total:03d}] {task.title}"
                f" - dependency rebuilt, re-running[/yellow]"
            )
        elif stored and stored.input_hash == current_input_hash:
            # Input unchanged - verify output integrity
            current_output_hash = compute_output_hash(stored.created_files, self.config)
            if stored.output_hash == current_output_hash:
                self.console.log(f"  [dim][{task.id}/{self.total:03d}] {task.title} (done)[/dim]")
                self._maybe_extract_interface(task, status)
                return True
            self.console.log(
                f"  [yellow][{task.id}/{self.total:03d}] {task.title}"
                f" - output modified, re-running[/yellow]"
            )
        elif stored:
            self.console.log(
                f"  [yellow][{task.id}/{self.total:03d}] {task.title}"
                f" - input changed, re-running[/yellow]"
            )
        else:
            # No stored state - trust DONE status, backfill hashes
            created_files = get_task_created_files(task, self.tasks_dir)
            self._store_task_state(task, prompt, created_files)
            self.console.log(f"  [dim][{task.id}/{self.total:03d}] {task.title} (done)[/dim]")
            self._maybe_extract_interface(task, status)
            return True

        task.status = TaskStatus.PENDING
        self._save_plan()
        return False

    def _run_one(self, task: PlanTask, status: Status) -> bool:
        """Run or skip one task. Returns False when the build should stop."""
        if task.status == TaskStatus.SKIPPED:
            self.console.log(f"  [dim][{task.id}/{self.total:03d}] {task.title} (skipped)[/dim]")
            return True

        if task.status == TaskStatus.DONE and self._cached_task_stands(task, status):
            return True

        if task.status == TaskStatus.MANUAL:
            self.console.log(
                f"  [yellow][{task.id}/{self.total:03d}] {task.title} - MANUAL (skipping)[/yellow]"
            )
            return True

        # Handle 'skip next' from the interactive prompt
        if self.skip_next:
            self.skip_next = False
            task.status = TaskStatus.SKIPPED
            self._save_plan()
            self.console.log(
                f"  [dim][{task.id}/{self.total:03d}] {task.title} (skipped by user)[/dim]"
            )
            return True

        task_status_map = {t.id: t.status for t in self.plan.tasks}
        unmet = [d for d in task.depends_on if task_status_map.get(d) != TaskStatus.DONE]
        if unmet:
            self.console.print()
            self.console.log(f"  [red]x [{task.id}/{self.total:03d}] {task.title}[/red]")
            self.console.log(f"    [red]Dependencies not met: {', '.join(unmet)}[/red]")
            task.status = TaskStatus.FAILED
            self._save_plan()
            if self.mode == BuildMode.AUTO_SKIP:
                return True
            self.stopped = True
            return False

        _print_task_header(self.console, task, self.total, self.verbose)

        # Assembled once - reused for build, retry, and hash storage
        prompt = self._assemble_prompt(task)

        result = self._dispatch_with_recovery(task, prompt, status)
        if result is None:
            return not self.stopped

        self.total_usage += result.usage
        if result.success:
            self._record_success(task, prompt, result)
        else:
            task.status = TaskStatus.FAILED
        self._save_plan()

        if not result.success:
            return self._handle_failure(task, prompt, status)

        self._maybe_extract_interface(task, status)

        if self.mode == BuildMode.STEP:
            status.stop()
            action = _prompt_after_success(self.console)
            status.start()
            if action == "quit":
                self.stopped = True
                return False
            if action == "skip":
                self.skip_next = True
        return True

    def _dispatch_with_recovery(
        self, task: PlanTask, prompt: str, status: Status
    ) -> TaskResult | None:
        """Run the task, handling LLM errors per mode.

        None means the task did not produce a result; self.stopped then says
        whether the whole build stops or moves on."""
        while True:
            try:
                return _run_task_dispatch(
                    task,
                    self.config,
                    prompt,
                    self.console,
                    status,
                    self.verbose,
                    self.plan,
                    self.smd_map,
                    self.amd_by_spec,
                )
            except AgentRunError as e:
                task.status = TaskStatus.FAILED
                self._save_plan()
                status.stop()
                _print_llm_error(self.console, task, self.total, e)

                if self.mode == BuildMode.AUTO_SKIP:
                    self.console.log(
                        f"  [red]x [{task.id}/{self.total:03d}] {task.title} "
                        f"(LLM error, continuing)[/red]"
                    )
                    status.start()
                    return None

                if self.mode == BuildMode.AUTO:
                    self.stopped = True
                    status.start()
                    return None

                action = _prompt_after_failure(self.console)
                status.start()
                if action == "retry":
                    task.status = TaskStatus.PENDING
                    self._save_plan()
                    _print_task_header(self.console, task, self.total, self.verbose)
                    continue
                if action == "skip":
                    task.status = TaskStatus.SKIPPED
                    self._save_plan()
                    self.console.log(
                        f"  [dim][{task.id}/{self.total:03d}] {task.title} (skipped)[/dim]"
                    )
                    return None
                self.stopped = True
                return None

    def _record_success(
        self, task: PlanTask, prompt: str, result: TaskResult, retry: bool = False
    ) -> None:
        task.status = TaskStatus.DONE
        label = f"{task.title} (retry)" if retry else task.title
        self.console.log(
            f"  [green]v [{task.id}/{self.total:03d}] {label}[/green]"
            f"  [dim]({result.summary()})[/dim]"
        )
        self.rebuilt_specs.add(task.spec)
        self.rebuilt_tasks.add(task.id)
        self._store_task_state(task, prompt, result.created_files, result.edited_files)

    def _handle_failure(self, task: PlanTask, prompt: str, status: Status) -> bool:
        """Per-mode handling after a failed task. Returns False to stop."""
        if self.mode == BuildMode.AUTO_SKIP:
            self.console.log(
                f"  [red]x [{task.id}/{self.total:03d}] {task.title} (failed, continuing)[/red]"
            )
            return True

        if self.mode == BuildMode.AUTO:
            self._print_build_stopped(task, "failed; the error output is above.")
            self.stopped = True
            return False

        # DEFAULT and STEP: interactive failure prompt
        status.stop()
        action = _prompt_after_failure(self.console)
        status.start()

        if action == "skip":
            task.status = TaskStatus.SKIPPED
            self._save_plan()
            self.console.log(
                f"  [dim][{task.id}/{self.total:03d}] {task.title} (skipped by user)[/dim]"
            )
            return True

        if action != "retry":
            self._print_build_stopped(task, "failed; the error output is above.")
            self.stopped = True
            return False

        task.status = TaskStatus.PENDING
        self._save_plan()
        _print_task_header(self.console, task, self.total, self.verbose)
        try:
            retry_result = _run_task_dispatch(
                task,
                self.config,
                prompt,
                self.console,
                status,
                self.verbose,
                self.plan,
                self.smd_map,
                self.amd_by_spec,
            )
        except AgentRunError as e:
            task.status = TaskStatus.FAILED
            self._save_plan()
            status.stop()
            _print_llm_error(self.console, task, self.total, e)
            status.start()
            self.stopped = True
            return False

        self.total_usage += retry_result.usage
        if retry_result.success:
            self._record_success(task, prompt, retry_result, retry=True)
        else:
            task.status = TaskStatus.FAILED
            self._print_build_stopped(task, "still failing.")
            self.stopped = True
        self._save_plan()
        if retry_result.success:
            self._maybe_extract_interface(task, status)
        return not self.stopped

    def _print_build_stopped(self, task: PlanTask, headline: str) -> None:
        self.console.print()
        self.console.print(
            Panel(
                f"Task [bold]{task.id}[/bold] {headline}\n"
                f"Review: [cyan].ossature/tasks/{task.id}-*/[/cyan]\n"
                f"Resume: [cyan]ossature build[/cyan]",
                title="[bold red]Build Stopped[/bold red]",
                border_style="red",
                expand=False,
                box=box.ROUNDED,
            )
        )

    def _print_summary(self) -> None:
        done = sum(1 for t in self.plan.tasks if t.status == TaskStatus.DONE)
        built_this_run = done - self.completed_before
        failed = sum(1 for t in self.plan.tasks if t.status == TaskStatus.FAILED)
        skipped = sum(1 for t in self.plan.tasks if t.status == TaskStatus.SKIPPED)

        summary = Text()
        summary.append(f"  Done: {done}/{self.total}", style="bold green")
        if built_this_run > 0:
            summary.append(f"  (built {built_this_run} this run)", style="dim")
        if failed:
            summary.append(f"  Failed: {failed}", style="bold red")
        if skipped:
            summary.append(f"  Skipped: {skipped}", style="dim")
        if self.total_usage.requests > 0:
            summary.append(f"  LLM: {self.total_usage.format_usage()}", style="dim")

        self.console.print()
        self.console.print(
            Panel(
                summary,
                title=f"[bold]{self.config.name} v{self.config.version} - Build Complete[/bold]",
                expand=False,
                box=box.ROUNDED,
            )
        )


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

    # Run the setup command before the first task (only on fresh builds)
    state_filepath = config.metadata_path / "state.toml"
    has_prior_state = state_filepath.exists() and state_filepath.stat().st_size > 0
    has_completed = has_prior_state or any(t.status == TaskStatus.DONE for t in plan.tasks)
    if not has_completed and not run_setup(config, console):
        raise SystemExit(1)

    _BuildRun(config, plan, smd_map, amd_by_spec, console, plan_filepath, mode, verbose).run()
