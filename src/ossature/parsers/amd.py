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

_KNOWN_SECTIONS = frozenset(
    {"Overview", "Components", "Data Models", "Flow", "Dependencies", "Notes"}
)


_FENCE_OPEN_RE = re.compile(r"^ {0,3}```")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}`{3,}\s*$")


def _mask_code_blocks(text: str) -> str:
    """Blank out fenced code block lines, keeping offsets intact.

    Fences are paired line by line the way markdown renders them: a line
    starting with ``` opens a fence, only a bare ``` line closes it, and an
    unterminated fence runs to the end of the text. Masked characters
    (except newlines) become spaces, so positions found on the masked copy
    index correctly into the original text and line anchors still line up.
    """
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        fence_line = False
        if in_fence:
            if _FENCE_CLOSE_RE.match(line):
                in_fence = False
                fence_line = True
        elif _FENCE_OPEN_RE.match(line):
            in_fence = True
            fence_line = True
        out.append(" " * len(line) if fence_line or in_fence else line)
    return "\n".join(out)


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

    # Unrecognized H2 sections are ignored by the field lookups above, which
    # silently loses whatever the author wrote there. Surface them as
    # warnings so a stray heading (a misplaced '## Contracts:' for example)
    # does not go unnoticed.
    warnings: list[str] = []
    for name in sections:
        if name not in _KNOWN_SECTIONS:
            warning = f"Unknown section '## {name}' is ignored"
            if name.strip(":").strip().lower() == "contracts":
                warning += (
                    " (contracts go in a '**Contracts:**' line inside a"
                    " component, not a section heading)"
                )
            warnings.append(warning)

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
        warnings=warnings,
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

        # Field labels are matched on a copy with code blocks blanked out and
        # anchored to line starts, so a literal '**Contracts:**' inside an
        # interface docstring or mid-sentence in prose is not a marker.
        # Offsets on the masked copy are valid in the original body.
        masked = _mask_code_blocks(body)

        # @path
        path = ""
        path_end = 0
        if m := re.search(r"^@path:[ \t]*(.*)", masked, re.MULTILINE):
            path = body[m.start(1) : m.end(1)].strip()
            path_end = m.end()
        if not path:
            errors.append(f"Component '{comp_name}': missing @path")

        # Markers. Each marker's content runs until the next marker after it.
        interface_marker = re.search(r"^\*\*Interface:\*\*", masked, re.MULTILINE)
        contracts_marker = re.search(r"^\*\*Contracts:\*\*", masked, re.MULTILINE)
        depends_marker = re.search(r"^\*\*Depends on:\*\*", masked, re.MULTILINE)
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

        # Contracts: required on every component, either an explicit 'None'
        # or a dash bullet list, bounded by the next marker. A wrapped
        # bullet continues on following non-blank lines (markdown lazy
        # continuation), so prose-length contracts survive intact.
        contracts: list[str] = []
        if not contracts_marker:
            errors.append(
                f"Component '{comp_name}': missing **Contracts:** (write "
                f"'**Contracts:** None' if the component has no behavioral "
                f"contracts)"
            )
        else:
            # The region is sliced from the masked copy so a fenced example
            # inside the section degrades to blank lines instead of leaking
            # code lines into contract text.
            region = _marker_region(masked, contracts_marker, marker_starts)
            stripped_region = region.strip()
            first_line = stripped_region.splitlines()[0].strip() if stripped_region else ""
            explicit_none = first_line.lower() == "none"
            items: list[list[str]] = []
            open_item = False
            for line in region.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    items.append([stripped.removeprefix("- ").strip()])
                    open_item = True
                elif stripped.startswith(("* ", "+ ")):
                    # A different bullet glyph starts its own (unrecognized)
                    # item; it must not be glued into the previous contract.
                    open_item = False
                elif stripped and open_item:
                    items[-1].append(stripped)
                else:
                    open_item = False
            contracts = [joined for parts in items if (joined := " ".join(parts).strip())]
            if explicit_none:
                # Content after the None line would be silently lost, so it
                # is rejected the same way None-plus-bullets is.
                if contracts:
                    errors.append(
                        f"Component '{comp_name}': **Contracts:** is 'None' "
                        f"but also lists bullet items"
                    )
                elif any(ln.strip() for ln in stripped_region.splitlines()[1:]):
                    errors.append(
                        f"Component '{comp_name}': **Contracts:** is 'None' "
                        f"but is followed by more content"
                    )
                contracts = []
            elif not contracts:
                errors.append(
                    f"Component '{comp_name}': **Contracts:** section needs "
                    f"at least one '- ' bullet item or 'None'"
                )

        # Depends on: the first non-empty line after the marker.
        depends_on: list[str] = []
        if depends_marker:
            region = _marker_region(masked, depends_marker, marker_starts)
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
