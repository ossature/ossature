import re
from pathlib import Path
from typing import Any

from ossature.models.shared import Status
from ossature.models.smd import Example, Priority, Requirement, SMDSpec
from ossature.parsers.common import SpecParseError, find_h1_title, split_sections, status_error
from ossature.parsers.frontmatter import FrontmatterError, split_frontmatter


class SMDParseError(SpecParseError):
    format_name = "SMD"


def parse_smd(text: str) -> SMDSpec:
    errors: list[str] = []

    try:
        meta, body = split_frontmatter(text)
    except FrontmatterError as e:
        raise SMDParseError([str(e)]) from None

    lines = body.strip().splitlines()

    title, idx = find_h1_title(lines)
    if not title:
        errors.append("Missing H1 title")

    for key in ("id", "status", "priority"):
        if not meta.get(key):
            errors.append(f"Missing required metadata: {key}")

    if err := status_error(meta.get("status")):
        errors.append(err)

    priority_values = {e.value for e in Priority}
    if (pv := meta.get("priority")) and pv not in priority_values:
        errors.append(
            f"Invalid priority: '{pv}'. Expected one of: {', '.join(sorted(priority_values))}"
        )

    depends = _coerce_depends(meta.get("depends", []))

    sections = split_sections(lines[idx:])

    overview = sections.get("Overview", "").strip()
    if not overview:
        errors.append("Missing or empty section: ## Overview")

    # Subsections
    requirements, req_errors = _parse_requirements(sections.get("Requirements", ""))
    errors.extend(req_errors)

    examples, ex_errors = _parse_examples(sections.get("Examples", ""))
    errors.extend(ex_errors)

    for section, label in (
        ("Goals", "goals"),
        ("Non-Goals", "non-goals"),
        ("Constraints", "constraints"),
        ("Acceptance Criteria", "acceptance criteria"),
    ):
        if not _parse_bullets(sections.get(section, "")):
            errors.append(f"Missing or empty section: {section} (need at least one {label} item)")

    if not requirements:
        errors.append("Missing or empty section: Requirements (need at least one requirement)")

    if not examples:
        errors.append("Missing or empty section: Examples (need at least one example)")

    # Bail if anything was wrong
    if errors:
        raise SMDParseError(errors)

    return SMDSpec(
        title=title,
        spec_id=str(meta.get("id", "")),
        status=Status(str(meta["status"])),
        priority=Priority(str(meta["priority"])),
        overview=overview,
        depends=depends,
        goals=_parse_bullets(sections.get("Goals", "")),
        non_goals=_parse_bullets(sections.get("Non-Goals", "")),
        requirements=requirements,
        constraints=_parse_bullets(sections.get("Constraints", "")),
        examples=examples,
        acceptance_criteria=_parse_bullets(sections.get("Acceptance Criteria", "")),
        notes=sections.get("Notes", "").strip(),
    )


def parse_smd_file(path: str | Path) -> SMDSpec:
    return parse_smd(Path(path).read_text(encoding="utf-8"))


def _coerce_depends(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _parse_bullets(text: str) -> list[str]:
    return [
        line.strip().removeprefix("- ").strip()
        for line in text.strip().splitlines()
        if line.strip().startswith("- ")
    ]


def _extract_field(body: str, name: str) -> str:
    if m := re.search(rf"\*\*{name}:\*\*\s*(.*)", body):
        return m.group(1).strip()
    return ""


_HEADING_MARKER_RE = re.compile(
    r"^(?P<title>.*?)\s*\{\s*(?:#(?P<anchor>[A-Za-z0-9_-]+))?\s*"
    r"(?P<noverify>\.no-verify)?\s*\}\s*$"
)


def _split_heading_markers(heading: str) -> tuple[str, str, bool]:
    """Split an optional trailing {#anchor .no-verify} marker off a heading.

    Returns (title, anchor, no_verify). A brace group carrying neither an
    anchor nor .no-verify is left in the title untouched.
    """
    m = _HEADING_MARKER_RE.match(heading)
    if not m or (not m.group("anchor") and not m.group("noverify")):
        return heading, "", False
    return m.group("title").strip(), m.group("anchor") or "", bool(m.group("noverify"))


def _parse_requirements(text: str) -> tuple[list[Requirement], list[str]]:
    reqs: list[Requirement] = []
    errors: list[str] = []

    for chunk in re.split(r"^### ", text, flags=re.MULTILINE):
        chunk = chunk.strip()
        if not chunk:
            continue

        heading, _, body = chunk.partition("\n")
        req_name, anchor, no_verify = _split_heading_markers(heading.strip())
        body = body.strip()

        accepts = _extract_field(body, "Accepts")
        returns = _extract_field(body, "Returns")

        if not accepts:
            errors.append(f"Requirement '{req_name}': missing **Accepts:**")
        if not returns:
            errors.append(f"Requirement '{req_name}': missing **Returns:**")

        # Description is everything before the first bold field marker
        first = re.search(r"\*\*(?:Accepts|Returns|Errors):\*\*", body)
        description = body[: first.start()].strip() if first else body.strip()

        if not description:
            errors.append(f"Requirement '{req_name}': missing description")

        # Errors: bullet list with "condition -> message"
        err_tuples: list[tuple[str, str]] = []
        if em := re.search(r"\*\*Errors:\*\*", body):
            for line in body[em.end() :].splitlines():
                line = line.strip()
                if not line.startswith("- "):
                    continue
                content = line.removeprefix("- ").strip()
                if "→" in content:
                    cond, _, msg = content.partition("→")
                elif "->" in content:
                    cond, _, msg = content.partition("->")
                else:
                    errors.append(
                        f"Requirement '{req_name}': error bullet missing arrow separator: "
                        f"'{content}'"
                    )
                    continue

                cond, msg = cond.strip(), msg.strip()
                if not cond or not msg:
                    errors.append(
                        f"Requirement '{req_name}': error bullet has empty condition or message: "
                        f"'{content}'"
                    )
                else:
                    err_tuples.append((cond, msg))

        reqs.append(
            Requirement(
                title=req_name,
                description=description,
                accepts=accepts,
                returns=returns,
                errors=err_tuples,
                anchor=anchor,
                no_verify=no_verify,
            )
        )

    return reqs, errors


def _parse_examples(text: str) -> tuple[list[Example], list[str]]:
    examples: list[Example] = []
    errors: list[str] = []
    code_block_re = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

    for chunk in re.split(r"^### ", text, flags=re.MULTILINE):
        chunk = chunk.strip()
        if not chunk:
            continue

        heading, _, body = chunk.partition("\n")
        ex_name = heading.strip()

        input_marker = re.search(r"\*\*Input:\*\*", body)
        output_marker = re.search(r"\*\*Output:\*\*", body)
        code_blocks = list(code_block_re.finditer(body))

        if not input_marker:
            errors.append(f"Example '{ex_name}': missing **Input:**")
        if not output_marker:
            errors.append(f"Example '{ex_name}': missing **Output:**")

        input_text = ""
        output_text = ""

        for cb in code_blocks:
            pos = cb.start()
            if (
                input_marker
                and not input_text
                and pos > input_marker.end()
                and (output_marker is None or pos < output_marker.start())
            ):
                input_text = cb.group(1).strip()
            if output_marker and not output_text and pos > output_marker.end():
                output_text = cb.group(1).strip()

        if input_marker and not input_text:
            errors.append(f"Example '{ex_name}': **Input:** has no code block")
        if output_marker and not output_text:
            errors.append(f"Example '{ex_name}': **Output:** has no code block")

        examples.append(Example(name=ex_name, input=input_text, output=output_text))

    return examples, errors
