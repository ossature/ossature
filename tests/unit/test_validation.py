from pathlib import Path

import pytest

from ossature.validation import (
    ValidationError,
    cross_check_specs,
    parse_specs,
    validate_specs,
)

SMD_TEMPLATE = """\
---
id: {spec_id}
status: draft
priority: high
depends: [{depends}]
---

# {spec_id} Module

## Overview

Overview text.

## Goals

- A goal

## Non-Goals

- A non-goal

## Requirements

### Core Requirement

Core requirement description.

**Accepts:** input

**Returns:** output

## Constraints

- A constraint

## Examples

### Basic Example

**Input:**

```
in
```

**Output:**

```
out
```

## Acceptance Criteria

- Works
"""


def _write_smd(tmp_path: Path, spec_id: str, depends: str = "") -> Path:
    path = tmp_path / f"{spec_id.lower()}.smd"
    path.write_text(SMD_TEMPLATE.format(spec_id=spec_id, depends=depends))
    return path


def _write_vmd(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.vmd"
    path.write_text(body)
    return path


class TestParseSpecs:
    def test_parses_without_cross_checks(self, tmp_path: Path) -> None:
        # A missing dependency is a cross-check failure, not a parse failure,
        # so parse_specs must return the models without raising.
        smd = _write_smd(tmp_path, "AUTH", depends="GHOST")
        parsed_smds, parsed_amds, parsed_vmds = parse_specs([smd], [], [])
        assert [s.spec_id for s in parsed_smds] == ["AUTH"]
        assert parsed_amds == []
        assert parsed_vmds == []


class TestCrossCheckSpecs:
    def test_accepts_valid_specs(self, tmp_path: Path) -> None:
        auth = _write_smd(tmp_path, "AUTH")
        api = _write_smd(tmp_path, "API", depends="AUTH")
        parsed = parse_specs([auth, api], [], [])
        cross_check_specs(*parsed)  # no raise

    def test_missing_dependency_raises(self, tmp_path: Path) -> None:
        smd = _write_smd(tmp_path, "AUTH", depends="GHOST")
        parsed = parse_specs([smd], [], [])
        with pytest.raises(ValidationError, match="doesn't exist"):
            cross_check_specs(*parsed)

    def test_cycle_raises(self, tmp_path: Path) -> None:
        a = _write_smd(tmp_path, "A", depends="B")
        b = _write_smd(tmp_path, "B", depends="A")
        parsed = parse_specs([a, b], [], [])
        with pytest.raises(ValidationError, match="Circular dependency"):
            cross_check_specs(*parsed)

    def test_duplicate_scenario_across_files_raises(self, tmp_path: Path) -> None:
        # The parser dedups scenarios within one file; the cross-check catches
        # the same scenario slug appearing across two VMDs for one spec.
        smd = _write_smd(tmp_path, "AUTH")
        vmd_a = _write_vmd(
            tmp_path, "auth1", "@spec AUTH\n\nscenario shared:\nwhen f(1)\nthen returns 1\n"
        )
        vmd_b = _write_vmd(
            tmp_path, "auth2", "@spec AUTH\n\nscenario shared:\nwhen f(2)\nthen returns 2\n"
        )
        parsed = parse_specs([smd], [], [vmd_a, vmd_b])
        with pytest.raises(ValidationError, match="duplicate scenario"):
            cross_check_specs(*parsed)


class TestValidateSpecs:
    def test_parses_and_cross_checks(self, tmp_path: Path) -> None:
        smd = _write_smd(tmp_path, "AUTH", depends="GHOST")
        with pytest.raises(ValidationError, match="doesn't exist"):
            validate_specs([smd], [], [])
