from pathlib import Path

import pytest

from ossature.models.shared import Status
from ossature.models.smd import Example, Priority, Requirement, SMDSpec
from ossature.renderer.smd import (
    render_example,
    render_requirement,
    render_smd,
    save_smd,
    save_smd_with_name,
)


def _make_spec(**overrides) -> SMDSpec:
    defaults = {
        "title": "Test Feature",
        "spec_id": "SMD_TEST_001",
        "status": Status.DRAFT,
        "priority": Priority.HIGH,
        "overview": "An overview.",
        "depends": [],
        "goals": [],
        "non_goals": [],
        "requirements": [
            Requirement(
                title="Req One",
                description="Req description.",
                accepts="string",
                returns="int",
            )
        ],
        "constraints": [],
        "examples": [],
        "acceptance_criteria": [],
        "notes": "",
    }
    defaults.update(overrides)
    return SMDSpec(**defaults)


class TestSMDRenderer:
    def test_render_requirement_without_errors(self):
        req = Requirement(
            title="My Req",
            description="Some description.",
            accepts="string input",
            returns="boolean result",
        )
        output = render_requirement(req)

        assert "### My Req" in output
        assert "Some description." in output
        assert "**Accepts:** string input" in output
        assert "**Returns:** boolean result" in output
        assert "**Errors:**" not in output

    def test_render_requirement_with_errors(self):
        req = Requirement(
            title="My Req",
            description="Some description.",
            accepts="string input",
            returns="boolean result",
            errors=[("empty input", "validation error"), ("bad format", "format error")],
        )
        output = render_requirement(req)

        assert "**Errors:**" in output
        assert "- empty input -> validation error" in output
        assert "- bad format -> format error" in output

    def test_render_example(self):
        ex = Example(name="Example One", input="some input", output="some output")
        output = render_example(ex)

        assert "### Example One" in output
        assert "**Input:**" in output
        assert "**Output:**" in output
        assert "```\nsome input\n```" in output
        assert "```\nsome output\n```" in output

    def test_render_smd_full(self):
        spec = _make_spec(
            goals=["Goal A", "Goal B"],
            non_goals=["Non-goal A"],
            requirements=[
                Requirement(
                    title="Req Alpha",
                    description="Alpha desc.",
                    accepts="string",
                    returns="int",
                    errors=[("bad", "error")],
                )
            ],
            constraints=["Constraint one"],
            examples=[Example(name="Ex One", input="i", output="o")],
            acceptance_criteria=["Criterion A"],
            notes="Some notes.",
            depends=["SMD-001"],
        )
        output = render_smd(spec)

        assert "# Test Feature" in output
        assert "@id: SMD_TEST_001" in output
        assert "@status: draft" in output
        assert "@priority: high" in output
        assert "@depends: [SMD-001]" in output
        assert "## Overview" in output
        assert "## Goals" in output
        assert "- Goal A" in output
        assert "- Goal B" in output
        assert "## Non-Goals" in output
        assert "- Non-goal A" in output
        assert "## Requirements" in output
        assert "### Req Alpha" in output
        assert "## Constraints" in output
        assert "- Constraint one" in output
        assert "## Examples" in output
        assert "### Ex One" in output
        assert "## Acceptance Criteria" in output
        assert "- [ ] Criterion A" in output
        assert "## Notes" in output
        assert "Some notes." in output

    def test_render_smd_empty_optional_sections(self):
        spec = _make_spec(
            goals=[],
            non_goals=[],
            constraints=[],
            examples=[],
            acceptance_criteria=[],
            notes="",
        )
        output = render_smd(spec)

        assert "## Goals" not in output
        assert "## Non-Goals" not in output
        assert "## Constraints" not in output
        assert "## Examples" not in output
        assert "## Acceptance Criteria" not in output
        assert "## Notes" in output

    def test_render_smd_depends(self):
        spec = _make_spec(depends=["SMD-001", "SMD-002"])
        output = render_smd(spec)

        assert "@depends: [SMD-001, SMD-002]" in output

    def test_render_smd_empty_depends(self):
        spec = _make_spec(depends=[])
        output = render_smd(spec)

        assert "@depends: []" in output

    def test_save_smd_creates_file(self, temp_dir: Path):
        spec = _make_spec()
        path = temp_dir / "spec.smd.md"
        result = save_smd(spec, path)

        assert result == path
        assert path.exists()
        assert path.read_text(encoding="utf-8") == render_smd(spec)

    def test_save_smd_creates_parent_dirs(self, temp_dir: Path):
        spec = _make_spec()
        path = temp_dir / "sub" / "dir" / "spec.smd.md"
        result = save_smd(spec, path)

        assert result == path
        assert path.exists()

    def test_save_smd_raises_if_exists(self, temp_dir: Path):
        spec = _make_spec()
        path = temp_dir / "spec.smd.md"
        path.write_text("existing", encoding="utf-8")

        with pytest.raises(FileExistsError):
            save_smd(spec, path)

    def test_save_smd_overwrites_when_flag_set(self, temp_dir: Path):
        spec = _make_spec()
        path = temp_dir / "spec.smd.md"
        path.write_text("existing", encoding="utf-8")

        result = save_smd(spec, path, overwrite=True)

        assert result == path
        assert path.read_text(encoding="utf-8") == render_smd(spec)

    def test_save_smd_with_name_default_filename(self, temp_dir: Path):
        spec = _make_spec(spec_id="SMD_TEST_001")
        result = save_smd_with_name(spec, temp_dir)

        expected_path = temp_dir / "smd-test-001.smd.md"
        assert result == expected_path
        assert expected_path.exists()

    def test_save_smd_with_name_custom_filename(self, temp_dir: Path):
        spec = _make_spec()
        result = save_smd_with_name(spec, temp_dir, filename="custom")

        expected_path = temp_dir / "custom.smd.md"
        assert result == expected_path
        assert expected_path.exists()

    def test_save_smd_with_name_already_suffixed(self, temp_dir: Path):
        spec = _make_spec()
        result = save_smd_with_name(spec, temp_dir, filename="custom.smd.md")

        expected_path = temp_dir / "custom.smd.md"
        assert result == expected_path
        assert expected_path.exists()
