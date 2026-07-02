import re
from dataclasses import dataclass, field
from pathlib import Path

from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.vmd import Group, VMDSpec
from ossature.verification.fixture import FIXTURE_DIR, fixture_filename, group_key


@dataclass
class VerifyTaskSpec:
    """A deterministic verify task synthesized from one VMD group.

    Merged into the plan after the spec's implementation tasks. The task
    serializes the group's cases to a fixture, generates the harness, and
    runs the real suite, so a passing build means the author's cases passed.
    """

    spec_id: str
    title: str
    description: str
    outputs: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    covers: list[str] = field(default_factory=list)
    vmd_file: str = ""
    vmd_group: str = ""
    target_file: str = ""


def resolve_target_file(group: Group, amds: list[AMDSpec]) -> str:
    """Find the AMD component path whose interface mentions the target."""
    for amd in amds:
        for comp in amd.components:
            if re.search(rf"\b{re.escape(group.name)}\b", comp.interface):
                return comp.path
    return ""


def harness_filename(group: Group, name_is_unique: bool, directory: str = "tests") -> str:
    base = f"test_checks_{group.name}"
    if group.kind == "cli":
        base += "_cli"
    elif not name_is_unique:
        base += f"_{group.arity}"
    return f"{directory}/{base}.py"


def _verify_command(config: OssatureConfig, harness_path: str) -> list[str]:
    if config.test.command:
        command = config.test.command
        if "{file}" in command:
            return [command.replace("{file}", harness_path)]
        return [f"{command} {harness_path}"]
    return [f"python -m pytest {harness_path} -q"]


def _uses_opaque_fixtures(group: Group) -> bool:
    return any(p.opaque_fixture for p in group.params)


def synthesize_verify_tasks(
    config: OssatureConfig,
    vmds_with_paths: list[tuple[Path, VMDSpec]],
    amd_by_spec: dict[str, list[AMDSpec]],
) -> tuple[dict[str, list[VerifyTaskSpec]], list[str]]:
    """Turn every VMD group into a pending verify task, grouped by spec.

    Returns (tasks_by_spec, warnings). Function groups need the python
    harness, so for other output languages they are skipped with a warning
    (their cases still count in the coverage ledger). Command (~cli) groups
    run for any output language: their harness invokes the built binary via
    subprocess, so only python and pytest need to be available at build
    time. Groups that need an opaque fixture are skipped with a warning:
    the harness cannot construct the handle deterministically yet.

    Harness placement follows the output language. Python projects get the
    harness under tests/ so the project's own test run picks it up; other
    languages get it under checks/ next to the fixture, out of the way of
    the language's test directory.
    """
    warnings: list[str] = []
    if not vmds_with_paths:
        return {}, warnings
    python_output = config.output.language == "python"
    harness_dir = "tests" if python_output else FIXTURE_DIR

    by_spec: dict[str, list[tuple[Path, VMDSpec, Group]]] = {}
    for path, vmd in vmds_with_paths:
        for group in vmd.groups:
            by_spec.setdefault(vmd.spec_id, []).append((path, vmd, group))

    result: dict[str, list[VerifyTaskSpec]] = {}
    for spec_id, entries in by_spec.items():
        value_name_counts: dict[str, int] = {}
        for _, _, group in entries:
            if group.kind == "value":
                value_name_counts[group.name] = value_name_counts.get(group.name, 0) + 1

        tasks: list[VerifyTaskSpec] = []
        for path, vmd, group in entries:
            if group.kind != "cli" and not python_output:
                warnings.append(
                    f"{spec_id}: group '{group.name}' targets a function, and "
                    f"deterministic harness generation supports python output "
                    f"only for now; skipping its verify task (its cases still "
                    f"count toward coverage)"
                )
                continue
            if _uses_opaque_fixtures(group):
                warnings.append(
                    f"{spec_id}: group '{group.name}' uses opaque fixtures, "
                    f"which deterministic harness generation does not support "
                    f"yet; skipping its verify task (cover it with a plain "
                    f"test task instead)"
                )
                continue
            try:
                vmd_rel = str(path.resolve().relative_to(config.root.resolve()))
            except ValueError:
                vmd_rel = str(path)
            unique = value_name_counts.get(group.name, 1) == 1
            harness = harness_filename(group, unique, harness_dir)
            fixture = f"{FIXTURE_DIR}/{fixture_filename(group)}"
            case_count = len(group.case_names)
            tasks.append(
                VerifyTaskSpec(
                    spec_id=spec_id,
                    title=f"Verify: {group.name}",
                    description=(
                        f"Run the {case_count} author-written verification "
                        f"case(s) for {group.name} from {vmd_rel}. The fixture "
                        f"and harness are generated deterministically; the "
                        f"expected values are author-owned."
                    ),
                    outputs=[fixture, harness],
                    verify=_verify_command(config, harness),
                    covers=list(group.covers),
                    vmd_file=vmd_rel,
                    vmd_group=group_key(group),
                    target_file=resolve_target_file(group, amd_by_spec.get(vmd.arch_id, [])),
                )
            )
        if tasks:
            result[spec_id] = tasks

    return result, warnings
