import hashlib
from pathlib import Path
from typing import Final

import tomli
import tomli_w

from ossature.config.loader import OssatureConfig
from ossature.models.audit import Manifest

CHECKSUM_ALGO: Final[str] = "sha256"


def _file_checksum(filepath: Path) -> str:
    hash_obj = hashlib.new(CHECKSUM_ALGO)

    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            hash_obj.update(chunk)

    return hash_obj.hexdigest()


def create_manifest(
    config: OssatureConfig,
    smd_files: list[Path],
    amd_files: list[Path],
    *,
    brief_inputs: dict[str, str] | None = None,
    project_brief_input: str = "",
) -> Manifest:
    sources: dict[str, str] = {}

    for smd_file in smd_files:
        smd_checksum = _file_checksum(smd_file)

        smd_filename = str(smd_file).replace(str(config.root), ".")
        sources[smd_filename] = f"{CHECKSUM_ALGO}:{smd_checksum}"

    for amd_file in amd_files:
        amd_checksum = _file_checksum(amd_file)

        amd_filename = str(amd_file).replace(str(config.root), ".")
        sources[amd_filename] = f"{CHECKSUM_ALGO}:{amd_checksum}"

    # Checksum for root config
    root_config_checksum = _file_checksum(config.root / "ossature.toml")
    sources["ossature.toml"] = f"{CHECKSUM_ALGO}:{root_config_checksum}"

    return Manifest(
        sources=sources,
        brief_inputs=dict(brief_inputs) if brief_inputs else {},
        project_brief_input=project_brief_input,
    )


def write_manifest(manifest: Manifest, filename: Path) -> None:
    with open(filename, "wb") as f:
        tomli_w.dump(manifest.model_dump(), f)


def read_manifest(filename: Path) -> Manifest | None:
    try:
        with open(filename, "rb") as f:
            data = tomli.load(f)
            return Manifest(**data)
    except tomli.TOMLDecodeError, FileNotFoundError, PermissionError:
        return None
