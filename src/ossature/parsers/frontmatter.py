from typing import Any

import yaml


class FrontmatterError(Exception):
    pass


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        raise FrontmatterError("Missing YAML frontmatter block (--- delimiters)")

    after_open = stripped[3:]
    if not after_open.startswith("\n") and not after_open.startswith("\r\n"):
        raise FrontmatterError("Missing YAML frontmatter block (--- delimiters)")

    after_open = after_open.lstrip("\n").lstrip("\r")
    end = _find_closing_fence(after_open)
    if end is None:
        raise FrontmatterError("Unterminated YAML frontmatter block")

    raw, body = after_open[:end], after_open[end:]
    body = body.split("\n", 1)[1] if "\n" in body else ""

    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise FrontmatterError(f"Invalid YAML in frontmatter: {e}") from None

    if not isinstance(meta, dict):
        raise FrontmatterError("Frontmatter must be a YAML mapping")

    return meta, body


def _find_closing_fence(text: str) -> int | None:
    pos = 0
    for line in text.splitlines(keepends=True):
        if line.rstrip("\r\n") == "---":
            return pos
        pos += len(line)
    return None
