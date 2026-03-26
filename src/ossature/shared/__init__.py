import json

from pydantic_ai import ModelRetry


def apply_edits(content: str, edits: list[dict[str, str]] | str) -> str:
    if isinstance(edits, str):
        try:
            parsed = json.loads(edits)
        except json.JSONDecodeError as e:
            raise ModelRetry(
                f"Could not parse edits JSON: {e}. "
                f"The `edits` parameter must be a valid JSON array of objects, e.g. "
                f'[{{"old": "old text", "new": "new text"}}]'
            )
    else:
        parsed = edits

    if not isinstance(parsed, list):
        raise ModelRetry(
            f"Expected a JSON array of edits, got {type(parsed).__name__}. "
            f'Use the format: [{{"old": "old text", "new": "new text"}}]'
        )

    if not parsed:
        raise ModelRetry("Edits array is empty — provide at least one edit.")

    for i, edit in enumerate(parsed):
        if not isinstance(edit, dict):
            raise ModelRetry(
                f"Edit #{i + 1} is not an object (got {type(edit).__name__}). "
                f'Each edit must be {{"old": "...", "new": "..."}}.'
            )
        if "old" not in edit or "new" not in edit:
            missing = [k for k in ("old", "new") if k not in edit]
            raise ModelRetry(
                f"Edit #{i + 1} is missing key(s): {', '.join(missing)}. "
                f'Each edit must have "old" and "new" keys.'
            )
        old, new = edit["old"], edit["new"]
        if not isinstance(old, str) or not isinstance(new, str):
            raise ModelRetry(f'Edit #{i + 1}: "old" and "new" must both be strings.')
        if old == new:
            raise ModelRetry(f"Edit #{i + 1}: old and new are identical — nothing to change.")

        count = content.count(old)
        if count == 0:
            # Show a short snippet of what's in the file to help the LLM
            raise ModelRetry(
                f"Edit #{i + 1} failed: the `old` text was not found in the file. "
                f"Make sure it matches the current file contents exactly "
                f"(including whitespace and indentation). "
                f"Use `read_file` or `grep_file` to check the current contents."
            )
        if count > 1:
            raise ModelRetry(
                f"Edit #{i + 1} failed: the `old` text matches {count} locations. "
                f"Include more surrounding context in `old` to make it unique."
            )

        content = content.replace(old, new, 1)

    return content
