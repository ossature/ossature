from pydantic import BaseModel
from pydantic_ai import ModelRetry


class FileEdit(BaseModel):
    """A single text replacement edit."""

    old: str
    new: str


def apply_edits(content: str, edits: list[FileEdit]) -> str:
    if not edits:
        raise ModelRetry("Edits array is empty — provide at least one edit.")

    for i, edit in enumerate(edits):
        if edit.old == edit.new:
            raise ModelRetry(f"Edit #{i + 1}: old and new are identical — nothing to change.")

        count = content.count(edit.old)
        if count == 0:
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

        content = content.replace(edit.old, edit.new, 1)

    return content
