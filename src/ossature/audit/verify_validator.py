"""Post-processing validation for planner-generated verify commands.

The planner can generate a task that runs a build command in its verify
step before any source file the build references exists yet. The classic
case is a scaffold task that emits only a manifest (`Cargo.toml`,
`build.zig`, `pyproject.toml`, and so on) and tries to run the build to
prove the manifest is right. The build fails because the source it
needs is produced by a later task.

This module walks a freshly generated SpecTaskPlan and flags those
cases so the planner can be asked to fix them. The check itself is
language-agnostic: it asks the active LanguageProfile two questions per
verify command (is this a build invocation, and does anything in the
chain produce a source file). A profile that leaves both lists empty,
like the generic fallback, gets no checks and no false positives.
"""

from dataclasses import dataclass

from ossature.models.plan import PlannerTask, PreservedTaskRef, SpecTaskPlan
from ossature.promptspec.profile import LanguageProfile


@dataclass(frozen=True, slots=True)
class VerifyValidationError:
    task_index: int  # 1-based index in the spec task list
    task_title: str
    verify_command: str
    reason: str


def check_verify_commands(
    plan: SpecTaskPlan,
    profile: LanguageProfile,
) -> list[VerifyValidationError]:
    """Return the verify commands that won't work given task ordering.

    A verify command is flagged when it contains one of the profile's
    build-invocation tokens but neither the task itself nor any of its
    `depends_on` predecessors have produced a file matching one of the
    profile's source extensions.

    Empty token or extension lists disable the check, so the generic
    profile and any language without curated data simply return no
    errors.
    """
    if not profile.build_invocation_tokens or not profile.source_extensions:
        return []

    # Outputs by 1-based index. PreservedTaskRefs have no inline outputs
    # so we skip them; a new task that depends only on preserved refs
    # falls into the same lookup gap and gets a conservative pass below.
    outputs_by_idx: dict[int, list[str]] = {
        i: task.outputs
        for i, task in enumerate(plan.tasks, start=1)
        if isinstance(task, PlannerTask)
    }

    errors: list[VerifyValidationError] = []

    for i, task in enumerate(plan.tasks, start=1):
        if not isinstance(task, PlannerTask):
            continue
        # Copy tasks ship verbatim assets, no verify, nothing to check.
        if task.source:
            continue

        # Collect outputs visible to this task's verify: its own, plus
        # every PlannerTask predecessor it depends on.
        visible_outputs: list[str] = list(task.outputs)
        for dep_idx in task.depends_on:
            if dep_idx in outputs_by_idx:
                visible_outputs.extend(outputs_by_idx[dep_idx])

        # If any predecessor is a preserved ref we don't have its outputs.
        # Assume it produced something useful and skip the check for this
        # task rather than emit a false positive.
        has_preserved_dependency = any(
            dep_idx not in outputs_by_idx
            and 1 <= dep_idx <= len(plan.tasks)
            and isinstance(plan.tasks[dep_idx - 1], PreservedTaskRef)
            for dep_idx in task.depends_on
        )
        if has_preserved_dependency:
            continue

        source_exists = _has_source_file(
            visible_outputs, profile.source_extensions, profile.manifest_filenames
        )

        for verify_cmd in task.verify:
            if not _command_invokes_build(verify_cmd, profile.build_invocation_tokens):
                continue
            if source_exists:
                continue
            errors.append(
                VerifyValidationError(
                    task_index=i,
                    task_title=task.title,
                    verify_command=verify_cmd,
                    reason=(
                        f"runs a build command but no {profile.name} source files "
                        f"({', '.join(profile.source_extensions)}) exist yet. The "
                        f"task and its depends_on predecessors have only produced "
                        f"{visible_outputs!r}."
                    ),
                )
            )

    return errors


def format_validator_errors(errors: list[VerifyValidationError]) -> str:
    """Format a list of errors as a single string for ModelRetry."""
    blocks: list[str] = []
    for err in errors:
        blocks.append(
            f"Task {err.task_index} ({err.task_title!r}) has a verify command "
            f"that won't succeed:\n"
            f"  command: {err.verify_command!r}\n"
            f"  reason: {err.reason}"
        )
    intro = (
        "Some verify commands won't succeed because they depend on files that "
        "do not exist yet at the point this task runs.\n\n"
    )
    outro = (
        "\n\nUpdate the affected tasks so their verify only references files "
        "produced by this task or one of its depends_on predecessors. For "
        "scaffold-only tasks, prefer `test -f <output>` or omit the verify "
        "entirely over invoking a build that has no source to compile."
    )
    return intro + "\n\n".join(blocks) + outro


def _command_invokes_build(cmd: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in cmd for tok in tokens)


def _has_source_file(
    outputs: list[str],
    extensions: tuple[str, ...],
    manifest_filenames: tuple[str, ...],
) -> bool:
    for out in outputs:
        if not any(out.endswith(ext) for ext in extensions):
            continue
        # Strip the leading path so the manifest check works whether the
        # file is at the root (`build.zig`) or nested (`src/build.zig`).
        basename = out.rsplit("/", 1)[-1]
        if basename in manifest_filenames:
            continue
        return True
    return False
