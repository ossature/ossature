import re
from dataclasses import dataclass, field

from ossature.models.plan import Plan
from ossature.models.smd import SMDSpec
from ossature.models.vmd import VMDSpec

# An error-type-looking token in a requirement's error bullets, like
# ValueError or StorageError. Used to derive which declared errors a
# covering group's !Type cases actually exercise.
_ERROR_TYPE_TOKEN_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b")


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


@dataclass
class RequirementCoverage:
    """Coverage state of one SMD requirement.

    groups lists the VMD group names that cover it; tasks lists the plan
    task ids that declare it in covers. Both count: a requirement verified
    only by a golden-file or roundtrip verify task is covered, not a hole.
    """

    spec_id: str
    title: str
    anchor: str = ""
    exempt: bool = False
    groups: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    declared_error_types: list[str] = field(default_factory=list)
    covered_error_types: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        return bool(self.groups or self.tasks)

    @property
    def missing_error_types(self) -> list[str]:
        return [t for t in self.declared_error_types if t not in self.covered_error_types]


@dataclass
class CoverageLedger:
    entries: list[RequirementCoverage] = field(default_factory=list)
    dangling: list[str] = field(default_factory=list)

    def uncovered(self) -> list[RequirementCoverage]:
        return [e for e in self.entries if not e.exempt and not e.covered]


def _declared_error_types(errors: list[tuple[str, str]]) -> list[str]:
    types: list[str] = []
    for condition, response in errors:
        for m in _ERROR_TYPE_TOKEN_RE.finditer(f"{condition} {response}"):
            if m.group(1) not in types:
                types.append(m.group(1))
    return types


def _resolve_target(
    target: str,
    by_anchor: dict[str, RequirementCoverage],
    by_title: dict[str, RequirementCoverage],
) -> RequirementCoverage | None:
    if target in by_anchor:
        return by_anchor[target]
    return by_title.get(_normalize(target))


def build_coverage_ledger(
    parsed_smds: list[SMDSpec],
    parsed_vmds: list[VMDSpec],
    plan: Plan | None = None,
) -> CoverageLedger:
    """Compute which requirements are verified, by what, with no LLM.

    A VMD group covers the requirements its @covers targets resolve to; a
    group with no @covers is inferred by a normalized name match against
    the requirement headings, and stays untagged when nothing matches. A
    plan task covers whatever its covers field resolves to. An explicit
    target that resolves to no heading is reported as dangling.
    """
    ledger = CoverageLedger()
    by_anchor: dict[str, dict[str, RequirementCoverage]] = {}
    by_title: dict[str, dict[str, RequirementCoverage]] = {}

    for smd in parsed_smds:
        anchors: dict[str, RequirementCoverage] = {}
        titles: dict[str, RequirementCoverage] = {}
        for req in smd.requirements:
            entry = RequirementCoverage(
                spec_id=smd.spec_id,
                title=req.title,
                anchor=req.anchor,
                exempt=req.no_verify,
                declared_error_types=_declared_error_types(req.errors),
            )
            ledger.entries.append(entry)
            if req.anchor:
                anchors[req.anchor] = entry
            titles[_normalize(req.title)] = entry
        by_anchor[smd.spec_id] = anchors
        by_title[smd.spec_id] = titles

    for vmd in parsed_vmds:
        anchors = by_anchor.get(vmd.spec_id, {})
        titles = by_title.get(vmd.spec_id, {})
        for group in vmd.groups:
            case_error_types = {c.error_type for c in group.cases if c.expect_kind == "error"}
            targets = group.covers
            inferred = False
            if not targets:
                targets = [group.name]
                inferred = True
            for target in targets:
                match = _resolve_target(target, anchors, titles)
                if match is None:
                    if not inferred:
                        ledger.dangling.append(
                            f"{vmd.spec_id}: group '{group.name}' covers "
                            f"'{target}', which matches no requirement"
                        )
                    continue
                if group.name not in match.groups:
                    match.groups.append(group.name)
                for etype in sorted(case_error_types):
                    if etype in match.declared_error_types and etype not in (
                        match.covered_error_types
                    ):
                        match.covered_error_types.append(etype)

    for task in plan.tasks if plan else []:
        if not task.covers:
            continue
        anchors = by_anchor.get(task.spec, {})
        titles = by_title.get(task.spec, {})
        for target in task.covers:
            match = _resolve_target(target, anchors, titles)
            if match is None:
                ledger.dangling.append(
                    f"{task.spec}: task {task.id} covers '{target}', which matches no requirement"
                )
                continue
            label = f"task:{task.id}"
            if label not in match.tasks:
                match.tasks.append(label)

    return ledger


@dataclass
class CoverageIssues:
    """Ledger findings split by severity handling.

    uncovered lines become errors when [test] require_coverage is on and
    warnings otherwise; advisory lines (dangling targets, missing error
    cases) are always warnings.
    """

    uncovered: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)


def format_coverage_issues(ledger: CoverageLedger) -> CoverageIssues:
    issues = CoverageIssues(advisory=list(ledger.dangling))
    for entry in ledger.uncovered():
        issues.uncovered.append(
            f"{entry.spec_id}: requirement '{entry.title}' has no covering "
            f"verification group or task (mark it {{.no-verify}} if that is intentional)"
        )
    for entry in ledger.entries:
        if entry.exempt or not entry.covered:
            continue
        for etype in entry.missing_error_types:
            issues.advisory.append(
                f"{entry.spec_id}: requirement '{entry.title}' declares "
                f"{etype} but no covering case expects !{etype}"
            )
    return issues
