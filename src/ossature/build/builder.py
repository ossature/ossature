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
    spec_by_task_id: dict[str, str] = {}
    for t in plan.tasks:
        spec_last_task_id[t.spec] = t.id
        spec_by_task_id[t.id] = t.spec

    # Track which specs already have interface files and which were rebuilt
    extracted_interfaces: set[str] = set()
    for sid in tasks_by_spec:
        if (config.metadata_context_interfaces_path / f"{sid}.md").exists():
            extracted_interfaces.add(sid)
    rebuilt_specs: set[str] = set()
    rebuilt_tasks: set[str] = set()
    # Specs whose interface file was successfully (re)extracted during this run.
    # A cross-spec dependent can rely on the input-hash gate only when its
    # upstream interface is fresh; if extraction failed or never ran, the file on
    # disk is stale and we fall back to forcing the dependent to rebuild.
    interface_refreshed_specs: set[str] = set()

    def _maybe_extract_interface(task: PlanTask, status: Status) -> None:
        if task.id != spec_last_task_id.get(task.spec):
            return
        if task.spec in extracted_interfaces and task.spec not in rebuilt_specs:
            return
        if not all(t.status == TaskStatus.DONE for t in tasks_by_spec[task.spec]):
            return
        try:
            written = extract_spec_interface(
                task.spec,
                plan,
                config,
                console,
                status,
                tracker=total_usage,
                amds=amd_by_spec.get(task.spec),
            )
        except AgentRunError as e:
            summary, _ = _describe_llm_error(e)
            console.log(
                f"  [yellow]Interface extraction failed for {task.spec}: {summary}[/yellow]"
            )
            return
        # Only mark the spec refreshed when a file was actually written. An
        # upstream that rebuilt but had no extractable source leaves a stale (or
        # absent) interface file on disk, so it stays out of both sets and
        # cross_spec_stale forces its dependents to rebuild.
        if not written:
            return
        extracted_interfaces.add(task.spec)
        interface_refreshed_specs.add(task.spec)

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
                if task.kind == "verify":
                    prompt = assemble_verify_task_prompt(task, config, plan, amd_by_spec)
                elif task.source:
                    prompt = assemble_copy_task_prompt(task, config)
                else:
                    prompt = assemble_task_prompt(
                        task,
                        config,
                        smd_map,
                        amd_by_spec,
                        final_outputs=final_output_paths(task, plan),
                    )
                current_input_hash = compute_input_hash(prompt, task, config)
                stored = state.get(task.id)

                # Same-spec dependencies travel as inject_files, which are
                # deliberately excluded from the input hash (see state.py), so a
                # same-spec rebuild must force this task to re-run. A dep id that
                # cannot be resolved to a spec defaults to same-spec, the
                # conservative direction that forces a rebuild rather than risk a
                # false skip.
                same_spec_rebuilt = any(
                    d in rebuilt_tasks
                    for d in task.depends_on
                    if spec_by_task_id.get(d, task.spec) == task.spec
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
                    sid in rebuilt_specs and sid not in interface_refreshed_specs
                    for sid in task.cross_spec_interfaces
                )
                dep_rebuilt = same_spec_rebuilt or cross_spec_stale

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
            if task.kind == "verify":
                prompt = assemble_verify_task_prompt(task, config, plan, amd_by_spec)
            elif task.source:
                prompt = assemble_copy_task_prompt(task, config)
            else:
                prompt = assemble_task_prompt(
                    task,
                    config,
                    smd_map,
                    amd_by_spec,
                    final_outputs=final_output_paths(task, plan),
                )

            # Run task with LLM error recovery
            llm_bail = False
            while True:
                try:
                    result = _run_task_dispatch(
                        task,
                        config,
                        prompt,
                        console,
                        status,
                        verbose,
                        plan,
                        smd_map,
                        amd_by_spec,
                    )
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

                    action = _prompt_after_failure(console)
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
                            f"Task [bold]{task.id}[/bold] failed; the error "
                            "output is above.\n"
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
                action = _prompt_after_failure(console)
                status.start()
                if action == "retry":
                    task.status = TaskStatus.PENDING
                    write_plan(plan, plan_filepath)
                    _print_task_header(console, task, total, verbose)
                    try:
                        retry_result = _run_task_dispatch(
                            task,
                            config,
                            prompt,
                            console,
                            status,
                            verbose,
                            plan,
                            smd_map,
                            amd_by_spec,
                        )
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
                            f"Task [bold]{task.id}[/bold] failed; the error "
                            "output is above.\n"
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
