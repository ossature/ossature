import re
from pathlib import Path

import pytest

from ossature.models.shared import Status
from ossature.models.smd import Example, Priority, Requirement, SMDSpec
from ossature.parsers.smd import SMDParseError, parse_smd, parse_smd_file
from ossature.renderer.smd import render_smd

VALID_SMD = """\
---
id: SMD-TEST-001
status: draft
priority: high
depends: [SMD-001]
---

# Test Feature

## Overview

This is the overview.

## Goals

- Goal one
- Goal two

## Non-Goals

- Non-goal one

## Requirements

### Req One

Description of req one.

**Accepts:** string input

**Returns:** boolean result

**Errors:**

- empty input -> return validation error
- invalid format \u2192 return format error

## Constraints

- Must be fast

## Examples

### Example One

**Input:**

```
some input
```

**Output:**

```
some output
```

## Acceptance Criteria

- [ ] Criterion one

## Notes

Some notes here.
"""

MINIMAL_VALID_HEADER = """\
---
id: SMD-001
status: draft
priority: high
depends: []
---

# Title

## Overview

Some overview.

"""


def _make_valid_with(**overrides: str) -> str:
    """Return MINIMAL_VALID_HEADER with metadata fields overridden."""
    text = MINIMAL_VALID_HEADER
    for key, value in overrides.items():
        text = re.sub(rf"^{key}:.*$", f"{key}: {value}", text, flags=re.MULTILINE)
    return text


def _minimal_with_sections(*extra_sections: str) -> str:
    """Return minimal valid header + extra H2 sections appended."""
    return MINIMAL_VALID_HEADER + "\n".join(extra_sections)


class TestSMDParser:
    def test_parse_valid_spec(self):
        spec = parse_smd(VALID_SMD)

        assert spec.title == "Test Feature"
        assert spec.spec_id == "SMD-TEST-001"
        assert spec.status == Status.DRAFT
        assert spec.priority == Priority.HIGH
        assert spec.overview == "This is the overview."
        assert spec.depends == ["SMD-001"]
        assert spec.goals == ["Goal one", "Goal two"]
        assert spec.non_goals == ["Non-goal one"]
        assert len(spec.requirements) == 1
        req = spec.requirements[0]
        assert req.title == "Req One"
        assert req.description == "Description of req one."
        assert req.accepts == "string input"
        assert req.returns == "boolean result"
        assert req.errors == [
            ("empty input", "return validation error"),
            ("invalid format", "return format error"),
        ]
        assert spec.constraints == ["Must be fast"]
        assert len(spec.examples) == 1
        assert spec.examples[0].name == "Example One"
        assert spec.examples[0].input == "some input"
        assert spec.examples[0].output == "some output"
        assert spec.acceptance_criteria == ["[ ] Criterion one"]
        assert spec.notes == "Some notes here."

    def test_parse_smd_file(self, temp_dir: Path):
        smd_file = temp_dir / "test.smd.md"
        smd_file.write_text(VALID_SMD, encoding="utf-8")

        spec = parse_smd_file(smd_file)
        assert spec.title == "Test Feature"
        assert spec.spec_id == "SMD-TEST-001"

    def test_missing_title(self):
        text = """\
---
id: SMD-001
status: draft
priority: high
---
"""
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Missing H1 title" in e for e in exc_info.value.errors)

    def test_missing_metadata(self):
        text = """\
---
{}
---

# Title

## Overview

Some overview.
"""
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        errors = exc_info.value.errors
        assert any("Missing required metadata: id" in e for e in errors)
        assert any("Missing required metadata: status" in e for e in errors)
        assert any("Missing required metadata: priority" in e for e in errors)

    def test_invalid_status(self):
        text = _make_valid_with(status="invalid_value")
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Invalid status" in e for e in exc_info.value.errors)

    def test_invalid_priority(self):
        text = _make_valid_with(priority="invalid_value")
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Invalid priority" in e for e in exc_info.value.errors)

    def test_missing_overview(self):
        text = """\
---
id: SMD-001
status: draft
priority: high
depends: []
---

# Title
"""
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Overview" in e for e in exc_info.value.errors)

    def test_missing_required_sections(self):
        text = MINIMAL_VALID_HEADER
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        errors = exc_info.value.errors
        assert any("Goals" in e for e in errors)
        assert any("Non-Goals" in e for e in errors)
        assert any("Constraints" in e for e in errors)
        assert any("Acceptance Criteria" in e for e in errors)
        assert any("Requirements" in e for e in errors)
        assert any("Examples" in e for e in errors)

    def test_requirement_missing_accepts(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\nSome description.\n\n**Returns:** something\n",
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("missing **Accepts:**" in e for e in exc_info.value.errors)

    def test_requirement_missing_returns(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\nSome description.\n\n**Accepts:** something\n",
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("missing **Returns:**" in e for e in exc_info.value.errors)

    def test_requirement_missing_description(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\n**Accepts:** something\n\n**Returns:** something\n",
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("missing description" in e for e in exc_info.value.errors)

    def test_requirement_error_with_arrow_separator(self):
        spec = parse_smd(VALID_SMD)
        req = spec.requirements[0]
        assert ("empty input", "return validation error") in req.errors

    def test_requirement_error_with_unicode_arrow(self):
        spec = parse_smd(VALID_SMD)
        req = spec.requirements[0]
        assert ("invalid format", "return format error") in req.errors

    def test_requirement_error_missing_arrow(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            (
                "## Requirements\n\n### Req\n\nDescription."
                "\n\n**Accepts:** x\n\n**Returns:** y"
                "\n\n**Errors:**\n\n- no arrow here\n"
            ),
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("missing arrow separator" in e for e in exc_info.value.errors)

    def test_requirement_error_empty_condition_or_message(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            (
                "## Requirements\n\n### Req\n\nDescription.\n\n"
                "**Accepts:** x\n\n**Returns:** y"
                "\n\n**Errors:**\n\n-  -> \n"
            ),
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("empty condition or message" in e for e in exc_info.value.errors)

    def test_example_missing_input_marker(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\nDesc.\n\n**Accepts:** x\n\n**Returns:** y\n",
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("missing **Input:**" in e for e in exc_info.value.errors)

    def test_example_missing_output_marker(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\nDesc.\n\n**Accepts:** x\n\n**Returns:** y\n",
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("missing **Output:**" in e for e in exc_info.value.errors)

    def test_example_missing_input_code_block(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\nDesc.\n\n**Accepts:** x\n\n**Returns:** y\n",
            "## Constraints\n\n- Constraint\n",
            "## Examples\n\n### Ex\n\n**Input:**\n\n**Output:**\n\n```\no\n```\n",
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("has no code block" in e for e in exc_info.value.errors)

    def test_example_missing_output_code_block(self):
        text = _minimal_with_sections(
            "## Goals\n\n- Goal\n",
            "## Non-Goals\n\n- Non-goal\n",
            "## Requirements\n\n### Req\n\nDesc.\n\n**Accepts:** x\n\n**Returns:** y\n",
            "## Constraints\n\n- Constraint\n",
            (
                "## Examples\n\n### Ex\n\n**Input:**\n\n```\ni\n```\n\n"
                "**Output:**\n\nno code block here\n"
            ),
            "## Acceptance Criteria\n\n- [ ] Done\n",
        )
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("has no code block" in e for e in exc_info.value.errors)

    def test_depends_parsing(self):
        spec = parse_smd(VALID_SMD)
        assert spec.depends == ["SMD-001"]

        text = VALID_SMD.replace("depends: [SMD-001]", "depends: [SMD-001, SMD-002]")
        spec2 = parse_smd(text)
        assert spec2.depends == ["SMD-001", "SMD-002"]

    def test_depends_as_yaml_block_list(self):
        text = VALID_SMD.replace(
            "depends: [SMD-001]",
            "depends:\n  - SMD-001\n  - SMD-002",
        )
        spec = parse_smd(text)
        assert spec.depends == ["SMD-001", "SMD-002"]

    def test_empty_depends(self):
        text = VALID_SMD.replace("depends: [SMD-001]", "depends: []")
        spec = parse_smd(text)
        assert spec.depends == []

    def test_null_depends(self):
        text = VALID_SMD.replace("depends: [SMD-001]", "depends:")
        spec = parse_smd(text)
        assert spec.depends == []

    def test_comma_separated_depends_string(self):
        text = VALID_SMD.replace("depends: [SMD-001]", "depends: SMD-001, SMD-002")
        spec = parse_smd(text)
        assert spec.depends == ["SMD-001", "SMD-002"]

    def test_frontmatter_open_fence_without_newline(self):
        text = "---id: X\n# Title\n"
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Missing YAML frontmatter" in e for e in exc_info.value.errors)

    def test_missing_frontmatter(self):
        text = "# Title\n\n## Overview\n\nContent.\n"
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Missing YAML frontmatter" in e for e in exc_info.value.errors)

    def test_unterminated_frontmatter(self):
        text = "---\nid: X\n\n# Title\n"
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Unterminated YAML frontmatter" in e for e in exc_info.value.errors)

    def test_invalid_yaml_in_frontmatter(self):
        text = "---\nid: [unclosed\n---\n\n# Title\n"
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Invalid YAML in frontmatter" in e for e in exc_info.value.errors)

    def test_frontmatter_not_a_mapping(self):
        text = "---\n- a\n- b\n---\n\n# Title\n"
        with pytest.raises(SMDParseError) as exc_info:
            parse_smd(text)
        assert any("Frontmatter must be a YAML mapping" in e for e in exc_info.value.errors)

    def test_notes_section(self):
        spec = parse_smd(VALID_SMD)
        assert spec.notes == "Some notes here."

    def test_round_trip(self):
        original = SMDSpec(
            title="Round Trip Feature",
            spec_id="SMD-RT-001",
            status=Status.DRAFT,
            priority=Priority.HIGH,
            overview="Overview for round trip.",
            depends=["SMD-001", "SMD-002"],
            goals=["Goal A", "Goal B"],
            non_goals=["Non-goal A"],
            requirements=[
                Requirement(
                    title="Req Alpha",
                    description="Alpha description.",
                    accepts="string",
                    returns="int",
                    errors=[("bad input", "error msg")],
                )
            ],
            constraints=["Constraint one"],
            examples=[
                Example(
                    name="Ex Alpha",
                    input="input data",
                    output="output data",
                )
            ],
            acceptance_criteria=["Criterion A"],
            notes="Some round trip notes.",
        )

        rendered = render_smd(original)
        parsed = parse_smd(rendered)

        assert parsed.title == original.title
        assert parsed.spec_id == original.spec_id
        assert parsed.status == original.status
        assert parsed.priority == original.priority
        assert parsed.overview == original.overview
        assert parsed.depends == original.depends
        assert parsed.goals == original.goals
        assert parsed.non_goals == original.non_goals
        assert parsed.constraints == original.constraints
        assert parsed.notes == original.notes

        assert len(parsed.requirements) == len(original.requirements)
        assert parsed.requirements[0].title == original.requirements[0].title
        assert parsed.requirements[0].description == original.requirements[0].description
        assert parsed.requirements[0].accepts == original.requirements[0].accepts
        assert parsed.requirements[0].returns == original.requirements[0].returns
        assert parsed.requirements[0].errors == original.requirements[0].errors

        assert len(parsed.examples) == len(original.examples)
        assert parsed.examples[0].name == original.examples[0].name
        assert parsed.examples[0].input == original.examples[0].input
        assert parsed.examples[0].output == original.examples[0].output

        # acceptance_criteria gets "[ ] " prefix from render_smd's "- [ ] {criterion}"
        assert parsed.acceptance_criteria == [f"[ ] {c}" for c in original.acceptance_criteria]
