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

        # Markers
        interface_marker = re.search(r"\*\*Interface:\*\*", body)
        depends_marker = re.search(r"\*\*Depends on:\*\*", body)

        # Description: between @path and first marker
        desc_end = len(body)
        if interface_marker:
            desc_end = interface_marker.start()
        elif depends_marker:
            desc_end = depends_marker.start()
        description = body[path_end:desc_end].strip()

        if not description:
            errors.append(f"Component '{comp_name}': missing description")

        # Interface code block
        interface = ""
        interface_language = ""
        if interface_marker:
            search_start = interface_marker.end()
            search_end = depends_marker.start() if depends_marker else len(body)
            if cb := _CODE_BLOCK_RE.search(body[search_start:search_end]):
                interface_language = cb.group(1)
                interface = cb.group(2).strip()

        if not interface:
            errors.append(f"Component '{comp_name}': missing **Interface:** code block")

        # Depends on
        depends_on: list[str] = []
        if depends_marker:
            deps_text = body[depends_marker.end() :].strip()
            deps_line = deps_text.splitlines()[0].strip() if deps_text else ""
            if deps_line and not deps_line.lower().startswith("none"):
                depends_on = [d.strip() for d in deps_line.split(",") if d.strip()]

        components.append(
            Component(
                name=comp_name,
                path=path,
                description=description,
                interface=interface,
                interface_language=interface_language,
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
