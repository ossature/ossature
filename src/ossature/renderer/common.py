"""Shared renderer helpers."""

from pathlib import Path


def write_spec(content: str, path: Path, overwrite: bool = False) -> Path:
    """Write rendered spec content to path, refusing to clobber unless told."""
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
