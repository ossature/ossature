from pathlib import Path

import tomli
import tomli_w
from pydantic import ValidationError
from rich.console import Console

from ossature.config.loader import OssatureConfig
from ossature.models.amd import AMDSpec
from ossature.models.audit import Manifest
from ossature.models.smd import SMDSpec
from ossature.models.vmd import VMDSpec
from ossature.shared.hashing import hash_file, tag


def create_manifest(
    config: OssatureConfig,
    smd_files: list[Path],
    amd_files: list[Path],
    vmd_files: list[Path] | None = None,
    *,
    brief_inputs: dict[str, str] | None = None,
    project_brief_input: str = "",
) -> Manifest:
    sources: dict[str, str] = {}

    for spec_file in [*smd_files, *amd_files, *(vmd_files or [])]:
        key = config.rel_key(spec_file)
        sources[key] = tag(hash_file(spec_file))

    # Checksum for root config
    sources["ossature.toml"] = tag(hash_file(config.root / "ossature.toml"))

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
    except tomli.TOMLDecodeError, FileNotFoundError, PermissionError, ValidationError:
        return None


def check_and_update_manifest(
    console: Console,
    config: OssatureConfig,
    smd_files: list[Path],
    amd_files: list[Path],
    vmd_files: list[Path] | None = None,
) -> tuple[list[str] | None, Manifest]:
    """Returns (changed source keys or None if unchanged, current manifest).

    Brief input hashes from a prior manifest are carried forward so brief
    regeneration can compare against them after fix cycles.
    """
    config.metadata_path.mkdir(parents=True, exist_ok=True)
    manifest_path = config.metadata_path / "manifest.toml"

    old: Manifest | None = None
    if manifest_path.exists():
        console.log("Reading existing manifest")
        old = read_manifest(manifest_path)
        if not old:
            console.log("Malformed manifest. Disregarding.")

    new_manifest = create_manifest(
        config=config,
        smd_files=smd_files,
        amd_files=amd_files,
        vmd_files=vmd_files,
        brief_inputs=old.brief_inputs if old else None,
        project_brief_input=old.project_brief_input if old else "",
    )

    if old is not None:
        mismatched = new_manifest.diff(other=old)
        if mismatched:
            console.log("[red]Manifest changed")
            for source in mismatched:
                console.log(f"  {source} has changed")
            write_manifest(new_manifest, filename=manifest_path)
            console.log("Manifest updated")
            return mismatched, new_manifest
        else:
            console.log("[green]Manifest unchanged")
            return None, new_manifest

    write_manifest(new_manifest, filename=manifest_path)
    console.log("[green]Manifest written")
    return list(new_manifest.sources.keys()), new_manifest


def get_changed_spec_ids(
    changed_files: list[str],
    smd_files: list[Path],
    amd_files: list[Path],
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    config: OssatureConfig,
    vmd_files: list[Path] | None = None,
    parsed_vmds: list[VMDSpec] | None = None,
) -> set[str]:
    """Maps changed manifest source keys to spec IDs."""
    if "ossature.toml" in changed_files:
        return {smd.spec_id for smd in parsed_smds}

    file_to_spec: dict[str, str] = {}

    for smd_file, smd in zip(smd_files, parsed_smds, strict=True):
        key = config.rel_key(smd_file)
        file_to_spec[key] = smd.spec_id

    for amd_file, amd in zip(amd_files, parsed_amds, strict=True):
        key = config.rel_key(amd_file)
        file_to_spec[key] = amd.spec_id

    for vmd_file, vmd in zip(vmd_files or [], parsed_vmds or [], strict=True):
        key = config.rel_key(vmd_file)
        file_to_spec[key] = vmd.spec_id

    return {file_to_spec[f] for f in changed_files if f in file_to_spec}
