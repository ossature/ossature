import re
from pathlib import Path

from ossature.models.shared import Status
from ossature.models.smd import Example, Priority, Requirement, SMDSpec


class SMDParseError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Invalid SMD spec ({len(errors)} error(s)):\n{summary}")


def parse_smd(text: str) -> SMDSpec:
    errors: list[str] = []
    lines = text.strip().splitlines()

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

    # Metadata
    meta: dict[str, str] = {}
    while idx < len(lines):
        line = lines[idx].strip()
        if line.startswith("## "):
            break
        if m := re.match(r"^@([\w-]+):\s*(.*)", line):
            meta[m.group(1)] = m.group(2).strip()
        idx += 1

    for key in ("id", "status", "priority"):
        if not meta.get(key):
            errors.append(f"Missing required metadata: @{key}")

    status_values = {e.value for e in Status}
    if (sv := meta.get("status")) and sv not in status_values:
        errors.append(
            f"Invalid @status: '{sv}'. Expected one of: {', '.join(sorted(status_values))}"
        )

    priority_values = {e.value for e in Priority}
    if (pv := meta.get("priority")) and pv not in priority_values:
        errors.append(
            f"Invalid @priority: '{pv}'. Expected one of: {', '.join(sorted(priority_values))}"
        )

    depends_raw = meta.get("depends", "[]").strip("[] ")
    depends = [d.strip() for d in depends_raw.split(",") if d.strip()]

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
        spec_id=meta.get("id", ""),
        status=Status(meta["status"]),
        priority=Priority(meta["priority"]),
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
    return parse_smd(Path(path).read_text())


def _parse_bullets(text: str) -> list[str]:
    return [
        line.removeprefix("- ").strip()
        for line in text.strip().splitlines()
        if line.strip().startswith("- ")
    ]


def _extract_field(body: str, name: str) -> str:
    if m := re.search(rf"\*\*{name}:\*\*\s*(.*)", body):
        return m.group(1).strip()
    return ""


def _parse_requirements(text: str) -> tuple[list[Requirement], list[str]]:
    reqs: list[Requirement] = []
    errors: list[str] = []

    for chunk in re.split(r"^### ", text, flags=re.MULTILINE):
        chunk = chunk.strip()
        if not chunk:
            continue

        heading, _, body = chunk.partition("\n")
        req_name = heading.strip()
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
