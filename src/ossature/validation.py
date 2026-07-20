"""Spec validation: parsing plus cross-reference, cycle, and uniqueness
checks. No CLI or rendering here."""

import re
from pathlib import Path

from ossature.models.amd import AMDSpec
from ossature.models.smd import SMDSpec
from ossature.models.vmd import VMDSpec
from ossature.parsers.amd import parse_amd_file
from ossature.parsers.smd import parse_smd_file
from ossature.parsers.vmd import parse_vmd_file

MAX_REQUIREMENT_COMPLEXITY = 3000


class ValidationError(Exception):
    pass


_WHITE, _GRAY, _BLACK = 0, 1, 2


def _detect_cycle(dep_map: dict[str, list[str]]) -> list[str] | None:
    color: dict[str, int] = dict.fromkeys(dep_map, _WHITE)
    parent: dict[str, str | None] = dict.fromkeys(dep_map, None)

    def dfs(node: str) -> list[str] | None:
        color[node] = _GRAY
        for dep in dep_map.get(node, []):
            if dep not in color:
                continue
            if color[dep] == _GRAY:
                cycle = [dep, node]
                cur = node
                p = parent[cur]
                while p is not None and p != dep:
                    cycle.append(p)
                    cur = p
                    p = parent[cur]
                cycle.reverse()
                return cycle
            if color[dep] == _WHITE:
                parent[dep] = node
                result = dfs(dep)
                if result:
                    return result
        color[node] = _BLACK
        return None

    for node in dep_map:
        if color[node] == _WHITE:
            result = dfs(node)
            if result:
                return result
    return None


def parse_specs(
    smd_files: list[Path],
    amd_files: list[Path],
    vmd_files: list[Path] | None = None,
) -> tuple[list[SMDSpec], list[AMDSpec], list[VMDSpec]]:
    """Parse every spec file. Raises the parser errors on malformed files."""
    parsed_smds = [parse_smd_file(f) for f in smd_files]
    parsed_amds = [parse_amd_file(f) for f in amd_files]
    parsed_vmds = [parse_vmd_file(f) for f in vmd_files or []]
    return parsed_smds, parsed_amds, parsed_vmds


def cross_check_specs(
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    parsed_vmds: list[VMDSpec],
) -> None:
    """Cross-reference, cycle, and uniqueness checks on already-parsed specs.

    Raises ValidationError on the first problem found.
    """
    smd_spec_ids = [smd.spec_id for smd in parsed_smds]

    for smd in parsed_smds:
        for dep in smd.depends:
            if dep not in smd_spec_ids:
                raise ValidationError(
                    f"Spec {smd.spec_id} has dependency {dep} that doesn't exist."
                )

    dep_map = {smd.spec_id: list(smd.depends) for smd in parsed_smds}
    cycle = _detect_cycle(dep_map)
    if cycle:
        cycle_str = " -> ".join([*cycle, cycle[0]])
        raise ValidationError(f"Circular dependency detected: {cycle_str}")

    for amd in parsed_amds:
        if amd.spec_id not in smd_spec_ids:
            raise ValidationError(f"Architecture for spec {amd.spec_id} that doesn't exist.")

    # Component names must be unique across all AMDs for the same spec
    # (case-insensitive, matching how arch refs resolve components).
    seen_components: dict[str, set[str]] = {}
    for amd in parsed_amds:
        seen = seen_components.setdefault(amd.spec_id, set())
        for comp in amd.components:
            key = comp.name.lower()
            if key in seen:
                raise ValidationError(
                    f"Spec {amd.spec_id} has duplicate component name "
                    f"'{comp.name}' in its AMD file(s)."
                )
            seen.add(key)

    for vmd in parsed_vmds:
        if vmd.spec_id not in smd_spec_ids:
            raise ValidationError(f"Verification for spec {vmd.spec_id} that doesn't exist.")
        if vmd.arch_id != vmd.spec_id and vmd.arch_id not in smd_spec_ids:
            raise ValidationError(
                f"Verification for spec {vmd.spec_id} points @arch at "
                f"{vmd.arch_id}, which doesn't exist."
            )

    # Group signatures and scenario names must be unique across all VMDs for
    # the same spec, the same rule duplicate component names follow for AMDs.
    seen_groups: dict[str, set[tuple[str, int]]] = {}
    seen_scenarios: dict[str, set[str]] = {}
    for vmd in parsed_vmds:
        seen_keys = seen_groups.setdefault(vmd.spec_id, set())
        for group in vmd.groups:
            group_key = (group.name.lower(), group.arity)
            if group_key in seen_keys:
                raise ValidationError(
                    f"Spec {vmd.spec_id} has duplicate verification group "
                    f"'{group.name}' in its VMD file(s)."
                )
            seen_keys.add(group_key)
        seen_slugs = seen_scenarios.setdefault(vmd.spec_id, set())
        for scenario in vmd.scenarios:
            if scenario.slug in seen_slugs:
                raise ValidationError(
                    f"Spec {vmd.spec_id} has duplicate scenario "
                    f"'{scenario.name}' in its VMD file(s)."
                )
            seen_slugs.add(scenario.slug)


def validate_specs(
    smd_files: list[Path],
    amd_files: list[Path],
    vmd_files: list[Path] | None = None,
) -> tuple[list[SMDSpec], list[AMDSpec], list[VMDSpec]]:
    """Parse and cross-check every spec file in one call."""
    parsed = parse_specs(smd_files, amd_files, vmd_files)
    cross_check_specs(*parsed)
    return parsed


def requirement_complexity(smd: SMDSpec) -> int:
    return sum(
        len(r.description)
        + len(r.accepts)
        + len(r.returns)
        + sum(len(c) + len(m) for c, m in r.errors)
        for r in smd.requirements
    )


def _normalize_heading(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def find_vmd_target_issues(
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    parsed_vmds: list[VMDSpec],
) -> list[str]:
    """Check that each value group's target resolves to something real.

    With an AMD, the target must appear as a word in a component interface
    block. Interface text is opaque code, so a miss is a warning rather than
    an error. Without an AMD, the fallback is a normalized match against the
    SMD requirement headings.
    """
    interfaces: dict[str, str] = {}
    for amd in parsed_amds:
        joined = "\n".join(c.interface for c in amd.components)
        interfaces[amd.spec_id] = interfaces.get(amd.spec_id, "") + "\n" + joined
    req_titles = {
        smd.spec_id: [_normalize_heading(r.title) for r in smd.requirements] for smd in parsed_smds
    }

    issues: list[str] = []
    for vmd in parsed_vmds:
        interface_text = interfaces.get(vmd.arch_id)
        targets = [(f"group '{g.name}'", g.name) for g in vmd.groups]
        targets.extend(
            (f"scenario '{s.name}'", s.call.target)
            for s in vmd.scenarios
            if s.kind == "call" and s.call is not None
        )
        for label, target in targets:
            if interface_text is not None:
                if not re.search(rf"\b{re.escape(target)}\b", interface_text):
                    issues.append(
                        f"{vmd.spec_id}: {label} targets '{target}', which does "
                        f"not appear in the {vmd.arch_id} AMD interface(s)"
                    )
            elif _normalize_heading(target) not in req_titles.get(vmd.spec_id, []):
                issues.append(
                    f"{vmd.spec_id}: {label} targets '{target}', which has no "
                    f"AMD to bind to and matches no requirement heading"
                )
    return issues
