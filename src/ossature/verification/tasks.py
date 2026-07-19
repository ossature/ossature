import re
from dataclasses import dataclass, field
from pathlib import Path

from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.vmd import Group, Scenario, VMDSpec
from ossature.verification.fixture import (
    FIXTURE_DIR,
    SCENARIOS_GROUP,
    fixture_filename,
    group_key,
    scenarios_fixture_filename,
    spec_slug,
)


@dataclass
class VerifyTaskSpec:
    """A deterministic verify task synthesized from a VMD.

    Merged into the plan after the spec's implementation tasks. The task
    serializes the author's cases to a fixture, generates the harness, and
    runs the real suite, so a passing build means the author's cases passed.
    A task carries either one table group (vmd_group "name/arity") or one
    VMD file's scenario bundle (vmd_group "@scenarios").
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
    # Names of the cases the task runs, shown to the planner so it knows
    # which behavior the author already verifies. Scenario names only; a
    # table group is identified by its target function in the title.
    case_labels: list[str] = field(default_factory=list)


def resolve_target_file(group: Group, amds: list[AMDSpec]) -> str:
    """Find the AMD component path whose interface mentions the target."""
    for amd in amds:
        for comp in amd.components:
            if re.search(rf"\b{re.escape(group.name)}\b", comp.interface):
                return comp.path
    return ""


def _sanitize(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def harness_filename(
    group: Group, spec_id: str, name_is_unique: bool, directory: str = "tests"
) -> str:
    """Pytest harness path, namespaced by spec and sanitized so the file is
    an importable module even for group names with '.' or '-'."""
    base = f"test_checks_{spec_slug(spec_id)}_{_sanitize(group.name)}"
    if not name_is_unique:
        base += f"_{group.arity}"
    return f"{directory}/{base}.py"


def _safe_stem(path: Path) -> str:
    return _sanitize(path.stem)


def scenarios_harness_filename(stem: str, spec_id: str, directory: str = "tests") -> str:
    return f"{directory}/test_checks_scenarios_{spec_slug(spec_id)}_{stem}.py"


def _verify_command(config: OssatureConfig, harness_path: str) -> list[str]:
    if config.test.command:
        command = config.test.command
        if "{file}" in command:
            return [command.replace("{file}", harness_path)]
        return [f"{command} {harness_path}"]
    return [f"python -m pytest {harness_path} -q"]


def _uses_opaque_fixtures(group: Group) -> bool:
    return any(p.opaque_fixture for p in group.params)


def eligible_scenarios(vmd: VMDSpec, python_output: bool) -> tuple[list[Scenario], list[str]]:
    """Split a file's scenarios into runnable ones and skip reasons.

    Call scenarios need the python harness; command scenarios run for any
    output language. Scenarios that need an opaque fixture are skipped: the
    harness cannot construct the handle deterministically yet.
    """
    eligible: list[Scenario] = []
    reasons: list[str] = []
    for scenario in vmd.scenarios:
        if scenario.uses_opaque:
            reasons.append(
                f"{vmd.spec_id}: scenario '{scenario.name}' uses opaque fixtures, "
                f"which deterministic harness generation does not support yet; "
                f"skipping it (cover it with a plain test task instead)"
            )
        elif scenario.kind == "call" and not python_output:
            reasons.append(
                f"{vmd.spec_id}: scenario '{scenario.name}' calls a function, and "
                f"deterministic harness generation supports python output only "
                f"for now; skipping it (its cases still count toward coverage)"
            )
        else:
            eligible.append(scenario)
    return eligible, reasons


def synthesize_verify_tasks(
    config: OssatureConfig,
    vmds_with_paths: list[tuple[Path, VMDSpec]],
    amd_by_spec: dict[str, list[AMDSpec]],
) -> tuple[dict[str, list[VerifyTaskSpec]], list[str]]:
    """Turn every VMD group and scenario bundle into pending verify tasks.

    Returns (tasks_by_spec, warnings). Table groups need the python harness,
    so for other output languages they are skipped with a warning (their
    cases still count in the coverage ledger). Scenario bundles run whenever
    they hold at least one eligible scenario.

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
        # Counted on the sanitized name, which is what the harness filename
        # uses, so sanitization collisions also get the arity suffix
        name_counts: dict[str, int] = {}
        for _, _, group in entries:
            key = _sanitize(group.name)
            name_counts[key] = name_counts.get(key, 0) + 1

        tasks: list[VerifyTaskSpec] = []
        for path, vmd, group in entries:
            if not python_output:
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
            vmd_rel = _relative_to_root(path, config)
            unique = name_counts.get(_sanitize(group.name), 1) == 1
            harness = harness_filename(group, spec_id, unique, harness_dir)
            fixture = f"{FIXTURE_DIR}/{fixture_filename(group, spec_id)}"
            tasks.append(
                VerifyTaskSpec(
                    spec_id=spec_id,
                    title=f"Verify: {group.name}",
                    description=(
                        f"Run the {len(group.cases)} author-written verification "
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

    # Same-stem VMD files for one spec would collide on the stem alone;
    # those fall back to the sanitized relative path as the stem
    stem_counts: dict[tuple[str, str], int] = {}
    for path, vmd in vmds_with_paths:
        stem_key = (vmd.spec_id, _safe_stem(path))
        stem_counts[stem_key] = stem_counts.get(stem_key, 0) + 1

    for path, vmd in vmds_with_paths:
        if not vmd.scenarios:
            continue
        eligible, reasons = eligible_scenarios(vmd, python_output)
        warnings.extend(reasons)
        if not eligible:
            continue
        vmd_rel = _relative_to_root(path, config)
        stem = _safe_stem(path)
        if stem_counts.get((vmd.spec_id, stem), 1) > 1:
            stem = _sanitize(str(Path(vmd_rel).with_suffix("")))
        harness = scenarios_harness_filename(stem, vmd.spec_id, harness_dir)
        fixture = f"{FIXTURE_DIR}/{scenarios_fixture_filename(stem, vmd.spec_id)}"
        covers: list[str] = []
        for scenario in eligible:
            for target in scenario.covers:
                if target not in covers:
                    covers.append(target)
        result.setdefault(vmd.spec_id, []).append(
            VerifyTaskSpec(
                spec_id=vmd.spec_id,
                title=f"Verify: scenarios ({stem})",
                description=(
                    f"Run the {len(eligible)} author-written scenario(s) from "
                    f"{vmd_rel}. The fixture and harness are generated "
                    f"deterministically; the expected values are author-owned."
                ),
                outputs=[fixture, harness],
                verify=_verify_command(config, harness),
                covers=covers,
                vmd_file=vmd_rel,
                vmd_group=SCENARIOS_GROUP,
                case_labels=[s.name for s in eligible],
            )
        )

    return result, warnings


def _relative_to_root(path: Path, config: OssatureConfig) -> str:
    try:
        return str(path.resolve().relative_to(config.root.resolve()))
    except ValueError:
        return str(path)
