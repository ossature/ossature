import hashlib
from pathlib import Path
from typing import Final

HASH_ALGO: Final[str] = "sha256"


def new_hasher() -> hashlib._Hash:
    return hashlib.new(HASH_ALGO)


def tag(hexdigest: str) -> str:
    """The '<algo>:<hexdigest>' form stored in the manifest, brief inputs, and
    build state."""
    return f"{HASH_ALGO}:{hexdigest}"


def hash_file(path: Path) -> str:
    """Streaming hexdigest of a file's bytes (no algo tag)."""
    h = new_hasher()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()
