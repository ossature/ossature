from __future__ import annotations

import re
import time
from pathlib import PurePosixPath

from pydantic_ai.exceptions import AgentRunError
from rich.console import Console
from rich.status import Status

from ossature.build.commands import _format_verify_for_display, run_verify
from ossature.build.prompts import render_current_file
from ossature.build.state import make_task_slug
from ossature.build.task import (
    BuildBackend,
    DefaultBuildBackend,
    TaskResult,
    _print_verify_errors,
    save_task_output,
)
from ossature.build.tools import BuildContext
from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.plan import Plan, PlanTask
from ossature.models.vmd import Group, Scenario, VMDSpec
from ossature.parsers.vmd import VMDParseError, parse_vmd_file
from ossature.shared.llm import UsageTracker
from ossature.verification.fixture import (
    SCENARIOS_GROUP,
    group_key,
    serialize_group,
    serialize_scenarios,
)
from ossature.verification.harness import render_python_harness, render_scenarios_harness
from ossature.verification.tasks import eligible_scenarios


def _reparse_vmd(task: PlanTask, config: OssatureConfig) -> tuple[VMDSpec | None, str]:
    """Re-parse the task's VMD file. Returns (vmd, "") or (None, error)."""
    path = config.root / task.vmd_file
    if not path.exists():
        return None, f"VMD file not found: {task.vmd_file}"
    try:
        return parse_vmd_file(path), ""
    except VMDParseError as e:
        return None, f"VMD file {task.vmd_file} no longer parses: {e}"


def load_group(task: PlanTask, config: OssatureConfig) -> tuple[Group | None, str]:
    """Re-parse the task's VMD file and find its group. Returns (group, error)."""
    vmd, err = _reparse_vmd(task, config)
    if vmd is None:
        return None, err
    for group in vmd.groups:
        if group_key(group) == task.vmd_group:
            return group, ""
    return None, f"group '{task.vmd_group}' not found in {task.vmd_file}"


def load_scenarios(task: PlanTask, config: OssatureConfig) -> tuple[list[Scenario] | None, str]:
    """Re-parse the task's VMD file and collect its runnable scenarios.

    Eligibility is recomputed with the same rules synthesis used, so an
    edited file yields a consistent bundle (and a changed bundle changes the
    task's input hash, which re-runs it)."""
    vmd, err = _reparse_vmd(task, config)
    if vmd is None:
        return None, err
    eligible, _ = eligible_scenarios(vmd, config.output.language == "python")
    if not eligible:
        return None, f"no runnable scenarios left in {task.vmd_file}"
    return eligible, ""


def _module_from_path(path: str) -> str:
    parts = list(PurePosixPath(path).parts)
    if not parts or not parts[-1].endswith(".py"):
        return ""
    if parts[0] in ("src", "lib"):
        parts = parts[1:]
    if not parts or parts[0] == "tests":
        return ""
    parts[-1] = parts[-1].removesuffix(".py")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return ""
    return ".".join(parts)


def module_candidates(
    task: PlanTask,
    plan: Plan,
    target_names: list[str],
    amds: list[AMDSpec],
) -> list[str]:
    """Modules the harness tries, in order, to find the target callables.

    The AMD components that declare the targets come first; the rest are the
    spec's implementation outputs in plan order.
    """
    paths: list[str] = []
    for name in target_names:
        for amd in amds:
            for comp in amd.components:
                if re.search(rf"\b{re.escape(name)}\b", comp.interface) and comp.path not in paths:
                    paths.append(comp.path)
    for t in plan.tasks:
        if t.spec != task.spec or t.kind == "verify" or t.source:
            continue
        paths.extend(o for o in t.outputs if o.endswith(".py"))
    candidates: list[str] = []
    for p in paths:
        module = _module_from_path(p)
        if module and module not in candidates:
            candidates.append(module)
    return candidates


def _scenario_stem(task: PlanTask) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", PurePosixPath(task.vmd_file).stem)


def _call_targets(scenarios: list[Scenario]) -> list[str]:
    targets: list[str] = []
    for scenario in scenarios:
        if (
            scenario.kind == "call"
            and scenario.call is not None
            and scenario.call.target not in targets
        ):
            targets.append(scenario.call.target)
    return targets


def assemble_verify_task_prompt(
    task: PlanTask,
    config: OssatureConfig,
    plan: Plan,
    amd_by_spec: dict[str, list[AMDSpec]],
) -> str:
    """Build the deterministic synthetic prompt used as the input-hash seed.

    Embeds the canonical fixture serialization, so editing any case (but not
    a comment or alignment whitespace) invalidates the task, and the module
    candidates, so a binding change regenerates the harness.
    """
    lines = ["<verify_task>"]
    lines.append(f"id: {task.id}")
    lines.append(f"title: {task.title}")
    lines.append(f"vmd_file: {task.vmd_file}")
    lines.append(f"vmd_group: {task.vmd_group}")
    lines.append("outputs:")
    lines.extend(f"- {o}" for o in task.outputs)
    lines.append("verify:")
    lines.extend(f"- {v}" for v in task.verify)
    amds = amd_by_spec.get(task.spec, [])
    if task.vmd_group == SCENARIOS_GROUP:
        scenarios, error = load_scenarios(task, config)
        if scenarios is None:
            lines.append(f"error: {error}")
        else:
            lines.append("module_candidates:")
            lines.extend(
                f"- {m}" for m in module_candidates(task, plan, _call_targets(scenarios), amds)
            )
            lines.append("fixture:")
            lines.append(serialize_scenarios(scenarios).rstrip("\n"))
    else:
        group, error = load_group(task, config)
        if group is None:
            lines.append(f"error: {error}")
        else:
            lines.append("module_candidates:")
            lines.extend(f"- {m}" for m in module_candidates(task, plan, [group.name], amds))
            lines.append("fixture:")
            lines.append(serialize_group(group).rstrip("\n"))
    lines.append("</verify_task>")
    return "\n".join(lines)


def _implementation_files(task: PlanTask, plan: Plan, config: OssatureConfig) -> list[str]:
    """Files the fixer may inspect and edit when the author's cases fail:
    the spec's implementation outputs, never the fixture or the harness."""
    files: list[str] = []
    for t in plan.tasks:
        if t.spec != task.spec or t.kind == "verify" or t.source:
            continue
        for output in t.outputs:
            if output in files or output in task.outputs:
                continue
            if (config.output_path / output).exists():
                files.append(output)
    return files


def assemble_verify_fix_prompt(
    task: PlanTask,
    error_output: str,
    config: OssatureConfig,
    impl_files: list[str],
    verify_label: str,
) -> str:
    sections = [
        "<verify_task_context>\n"
        "The author-written verification cases for this project failed. The "
        "expected values are authoritative: they were written by the spec "
        "author and define correct behavior. Fix the "
        "implementation so the cases pass.\n"
        f"The fixture and harness files ({', '.join(task.outputs)}) are "
        "read-only and generated; do not edit them, and do not hardcode "
        "case-specific values in the implementation.\n"
        "</verify_task_context>",
        f"<error_output>\n```\n{error_output}\n```\n</error_output>",
    ]
    if verify_label:
        sections.append(f"<verify_command>\n{verify_label}\n</verify_command>")

    for filepath in impl_files:
        if block := render_current_file(filepath, config):
            sections.append(block)

    sections.append(f"<task>\n**{task.title}**: {task.description}\n</task>")
    return "\n\n".join(sections)


def build_verify_task(
    task: PlanTask,
    config: OssatureConfig,
    prompt: str,
    console: Console,
    status: Status,
    plan: Plan,
    amd_by_spec: dict[str, list[AMDSpec]],
    verbose: bool = False,
    *,
    backend: BuildBackend | None = None,
) -> TaskResult:
    """Execute a verify task: emit the fixture, generate the harness, run the
    real suite. No model touches the grading path; on failure a fixer agent
    is pointed at the implementation files, with the fixture and harness
    read-only."""
    slug = make_task_slug(task)
    task_dir = config.metadata_path / "tasks" / f"{task.id}-{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt.md").write_text(prompt)

    task_label = f"[{task.id}] {task.title}"
    status.update(f"{task_label} -- preparing verification...")

    t0 = time.monotonic()
    task_usage = UsageTracker()
    created_files: list[str] = []

    def _result(success: bool, edited: list[str] | None = None) -> TaskResult:
        return TaskResult(
            success=success,
            file_count=len(created_files) + len(edited or []),
            total_lines=0,
            elapsed=time.monotonic() - t0,
            created_files=created_files,
            edited_files=list(edited or []),
            usage=task_usage,
        )

    def _fail(message: str) -> TaskResult:
        (task_dir / "response.md").write_text(f"[verify task error] {message}\n")
        save_task_output(task_dir, created_files, [], False, message)
        console.log(f"    [red]{message}[/red]")
        return _result(False)

    amds = amd_by_spec.get(task.spec, [])
    if task.vmd_group == SCENARIOS_GROUP:
        scenarios, error = load_scenarios(task, config)
        if scenarios is None:
            return _fail(error)
        call_targets = _call_targets(scenarios)
        candidates = module_candidates(task, plan, call_targets, amds)
        if call_targets and not candidates:
            return _fail(
                f"no importable modules found for scenario targets "
                f"{', '.join(call_targets)}; cannot generate a harness"
            )
        fixture_text = serialize_scenarios(scenarios)
        harness_text = render_scenarios_harness(
            scenarios, _scenario_stem(task), task.outputs[0], candidates
        )
        case_count = len(scenarios)
    else:
        group, error = load_group(task, config)
        if group is None:
            return _fail(error)
        candidates = module_candidates(task, plan, [group.name], amds)
        if not candidates:
            return _fail(
                f"no importable modules found for target '{group.name}'; cannot generate a harness"
            )
        fixture_text = serialize_group(group)
        harness_text = render_python_harness(group, task.outputs[0], candidates)
        case_count = len(group.cases)

    fixture_rel, harness_rel = task.outputs[0], task.outputs[1]

    fixture_full = config.output_path / fixture_rel
    fixture_full.parent.mkdir(parents=True, exist_ok=True)
    fixture_full.write_text(fixture_text)
    created_files.append(fixture_rel)

    harness_full = config.output_path / harness_rel
    harness_full.parent.mkdir(parents=True, exist_ok=True)
    harness_full.write_text(harness_text)
    created_files.append(harness_rel)

    (task_dir / "response.md").write_text(
        f"Emitted fixture {fixture_rel} ({case_count} case(s)) and "
        f"generated harness {harness_rel}.\n"
    )

    verify_label = _format_verify_for_display(task.verify)
    status.update(f"{task_label} -- verifying ({verify_label})")
    passed, verify_output = run_verify(task.verify, config.output_path)
    if passed:
        save_task_output(task_dir, created_files, [], True, verify_output)
        return _result(True)

    # The expected values are right by definition, so the fixer is pointed
    # at the implementation files. The fixture and harness are protected:
    # the fixer cannot rewrite the grader to make the failure disappear.
    impl_files = _implementation_files(task, plan, config)
    if not impl_files:
        _print_verify_errors(console, verify_output)
        save_task_output(task_dir, created_files, [], False, verify_output)
        return _result(False)

    if backend is None:
        backend = DefaultBuildBackend(config)
    build_ctx = BuildContext(
        output_dir=config.output_path,
        console=console,
        status=status,
        verbose=verbose,
        context_dir=config.context_path if config.context_path.is_dir() else None,
        task_label=task_label,
        protected_paths=list(task.outputs),
    )
    build_model = config.llm.model_for("fixer")

    attempt = 0
    while attempt < config.build.max_fix_attempts:
        build_ctx.set_phase(f"-- fixing ({attempt + 1}/{config.build.max_fix_attempts})")
        fix_prompt = assemble_verify_fix_prompt(
            task, verify_output, config, impl_files, verify_label
        )
        (task_dir / f"fix-{attempt + 1}-prompt.md").write_text(fix_prompt)
        try:
            fix_output = backend.fix(
                fix_prompt, build_ctx, console, tracker=task_usage, model_name=build_model
            )
        except AgentRunError as e:
            (task_dir / f"fix-{attempt + 1}-response.md").write_text(f"[agent error] {e}")
            attempt += 1
            continue
        (task_dir / f"fix-{attempt + 1}-response.md").write_text(fix_output)

        build_ctx.set_phase(f"-- re-verifying ({verify_label})")
        passed, verify_output = run_verify(task.verify, config.output_path)
        if passed:
            save_task_output(task_dir, created_files, build_ctx.edited_files, True, verify_output)
            return _result(True, edited=build_ctx.edited_files)
        attempt += 1

    _print_verify_errors(console, verify_output)
    save_task_output(task_dir, created_files, build_ctx.edited_files, False, verify_output)
    return _result(False, edited=build_ctx.edited_files)
