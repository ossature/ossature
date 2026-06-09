import re
from pathlib import Path

from ossature.models.amd import AMDSpec, Component, DataModel, Dependency
from ossature.models.shared import Status
from ossature.parsers.frontmatter import FrontmatterError, split_frontmatter


class AMDParseError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Invalid AMD spec ({len(errors)} error(s)):\n{summary}")


_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def parse_amd(text: str) -> AMDSpec:
    errors: list[str] = []

    try:
        meta, body = split_frontmatter(text)
    except FrontmatterError as e:
        raise AMDParseError([str(e)]) from None

    lines = body.strip().splitlines()

    # H1 title
    title = ""
    idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line.removeprefix("# ").strip()
            idx = i + 1
            break
    if not title:
        errors.append("Missing H1 title")

    for key in ("spec", "status"):
        if not meta.get(key):
            errors.append(f"Missing required metadata: {key}")

    status_values = {e.value for e in Status}
    if (sv := meta.get("status")) and sv not in status_values:
        errors.append(
            f"Invalid status: '{sv}'. Expected one of: {', '.join(sorted(status_values))}"
        )

    # H2 sections
    sections: dict[str, str] = {}
    current_section: str | None = None
    section_lines: list[str] = []

    for line in lines[idx:]:
        if line.startswith("## ") and not line.startswith("### "):
            if current_section is not None:
                sections[current_section] = "\n".join(section_lines)
            current_section = line.removeprefix("## ").strip()
            section_lines = []
        else:
            section_lines.append(line)

    if current_section is not None:
        sections[current_section] = "\n".join(section_lines)

    overview = sections.get("Overview", "").strip()
    if not overview:
        errors.append("Missing or empty section: ## Overview")

    # Subsections
    components, comp_errors = _parse_components(sections.get("Components", ""))
    errors.extend(comp_errors)

    data_models, dm_errors = _parse_data_models(sections.get("Data Models", ""))
    errors.extend(dm_errors)

    dependencies, dep_errors = _parse_dependencies(sections.get("Dependencies", ""))
    errors.extend(dep_errors)

    if not components:
        errors.append("Missing or empty section: Components (need at least one component)")

    if errors:
        raise AMDParseError(errors)

    return AMDSpec(
        title=title,
        spec_id=str(meta.get("spec", "")),
        status=Status(str(meta["status"])),
        overview=overview,
        components=components,
        data_models=data_models,
        flow=sections.get("Flow", "").strip(),
        dependencies=dependencies,
        notes=sections.get("Notes", "").strip(),
    )


def parse_amd_file(path: str | Path) -> AMDSpec:
    return parse_amd(Path(path).read_text())


def _marker_region(body: str, marker: re.Match[str], marker_starts: list[int]) -> str:
    """Return the text a marker owns: from its label end to the next marker.

    The Interface, Contracts, and Depends-on markers each own the text up to
    whichever other marker comes next, so the three can appear in any order
    without one swallowing another.
    """
    later = [s for s in marker_starts if s > marker.start()]
    end = min(later) if later else len(body)
    return body[marker.end() : end]


def _parse_components(text: str) -> tuple[list[Component], list[str]]:
    components: list[Component] = []
    errors: list[str] = []

    for chunk in re.split(r"^### ", text, flags=re.MULTILINE):
        chunk = chunk.strip()
        if not chunk:
            continue

        heading, _, body = chunk.partition("\n")
        comp_name = heading.strip()
        body = body.strip()

        # @path
        path = ""
        path_end = 0
        if m := re.search(r"^@path:\s*(.*)", body, re.MULTILINE):
            path = m.group(1).strip()
            path_end = m.end()
        if not path:
            errors.append(f"Component '{comp_name}': missing @path")

        # Markers. Each marker's content runs until the next marker after it.
        interface_marker = re.search(r"\*\*Interface:\*\*", body)
        contracts_marker = re.search(r"\*\*Contracts:\*\*", body)
        depends_marker = re.search(r"\*\*Depends on:\*\*", body)
        marker_starts = sorted(
            m.start() for m in (interface_marker, contracts_marker, depends_marker) if m
        )

        # Description: between @path and the first marker (or end of body).
        desc_end = marker_starts[0] if marker_starts else len(body)
        description = body[path_end:desc_end].strip()
        if not description:
            errors.append(f"Component '{comp_name}': missing description")

        # Interface code block, bounded by the next marker.
        interface = ""
        interface_language = ""
        if interface_marker:
            region = _marker_region(body, interface_marker, marker_starts)
            if cb := _CODE_BLOCK_RE.search(region):
                interface_language = cb.group(1)
                interface = cb.group(2).strip()

        if not interface:
            errors.append(f"Component '{comp_name}': missing **Interface:** code block")

        # Contracts: an optional bullet list, bounded by the next marker. A
        # marker that is present but has no bullets is flagged rather than
        # silently dropped, mirroring how a missing interface block is caught.
        contracts: list[str] = []
        if contracts_marker:
            region = _marker_region(body, contracts_marker, marker_starts)
            for line in region.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    item = stripped.removeprefix("- ").strip()
                    if item:
                        contracts.append(item)
            if not contracts:
                errors.append(
                    f"Component '{comp_name}': **Contracts:** section is present "
                    f"but has no bullet items"
                )

        # Depends on: the first non-empty line after the marker.
        depends_on: list[str] = []
        if depends_marker:
            region = _marker_region(body, depends_marker, marker_starts)
            deps_line = region.strip().splitlines()[0].strip() if region.strip() else ""
            if deps_line and not deps_line.lower().startswith("none"):
                depends_on = [d.strip() for d in deps_line.split(",") if d.strip()]

        components.append(
            Component(
                name=comp_name,
                path=path,
                description=description,
                interface=interface,
                interface_language=interface_language,
                contracts=contracts,
                depends_on=depends_on,
            )
        )

    return components, errors


def _parse_data_models(text: str) -> tuple[list[DataModel], list[str]]:
    models: list[DataModel] = []
    errors: list[str] = []

    for chunk in re.split(r"^### ", text, flags=re.MULTILINE):
        chunk = chunk.strip()
        if not chunk:
            continue

        heading, _, body = chunk.partition("\n")
        model_name = heading.strip()
        body = body.strip()

        definition = ""
        definition_language = ""
        if cb := _CODE_BLOCK_RE.search(body):
            definition_language = cb.group(1)
            definition = cb.group(2).strip()

        if not definition:
            errors.append(f"Data model '{model_name}': missing code block definition")

        models.append(
            DataModel(
                name=model_name,
                definition=definition,
                definition_language=definition_language,
            )
        )

    return models, errors


def _parse_dependencies(text: str) -> tuple[list[Dependency], list[str]]:
    deps: list[Dependency] = []
    errors: list[str] = []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        content = line.removeprefix("- ").strip()
        if ":" not in content:
            errors.append(
                f"Dependency '{content}': missing colon separator (expected 'name: purpose')"
            )
            continue
        name, _, purpose = content.partition(":")
        name, purpose = name.strip(), purpose.strip()
        if not name or not purpose:
            errors.append(f"Dependency bullet has empty name or purpose: '{content}'")
        else:
            deps.append(Dependency(name=name, purpose=purpose))

    return deps, errors
