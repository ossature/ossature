"""Shared parser machinery: the parse-error base, fence matching, and the
markdown structure helpers used by the SMD and AMD parsers."""

import re

from ossature.models.shared import Status


class SpecParseError(Exception):
    """Base for the per-format parse errors. Subclasses set ``format_name``."""

    format_name = "spec"

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Invalid {self.format_name} spec ({len(errors)} error(s)):\n{summary}")


# A line starting with ``` opens a fence; only a bare ``` line closes it.
FENCE_OPEN_RE = re.compile(r"^ {0,3}```")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}`{3,}\s*$")


def find_h1_title(lines: list[str]) -> tuple[str, int]:
    """Return (title, index-after-the-H1). ('', 0) when there is no H1."""
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return line.removeprefix("# ").strip(), i + 1
    return "", 0


def status_error(value: str | None) -> str | None:
    """Error message for an invalid status value, or None when valid/absent."""
    if not value:
        return None
    valid = {e.value for e in Status}
    if value not in valid:
        return f"Invalid status: '{value}'. Expected one of: {', '.join(sorted(valid))}"
    return None


def split_sections(lines: list[str]) -> dict[str, str]:
    """Split lines into H2 sections, fence-aware.

    A '## ' line inside a fenced code block is content, not a heading, so
    fence state is tracked across the split.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    in_fence = False
    for line in lines:
        if in_fence:
            buf.append(line)
            if FENCE_CLOSE_RE.match(line):
                in_fence = False
        elif FENCE_OPEN_RE.match(line):
            buf.append(line)
            in_fence = True
        elif line.startswith("## ") and not line.startswith("### "):
            if current is not None:
                sections[current] = "\n".join(buf)
            current = line.removeprefix("## ").strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections
