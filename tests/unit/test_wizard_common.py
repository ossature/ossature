from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from ossature.cli.wizard import common as wizard

VALID_SMD = """\
---
id: AUTH
status: draft
priority: high
depends: []
---

# Auth

## Overview

Overview text.

## Goals

- Goal one

## Non-Goals

- Non-goal one

## Requirements

### Req One

Description.

**Accepts:** input

**Returns:** output

## Constraints

- A constraint

## Examples

### Example One

**Input:**

```
in
```

**Output:**

```
out
```

## Acceptance Criteria

- [ ] Criterion one
"""


class _Color(Enum):
    RED = "red"
    BLUE = "blue"


class TestEnumChoices:
    def test_returns_one_choice_per_member(self):
        choices = wizard.enum_choices(_Color)
        assert [c.title for c in choices] == ["red", "blue"]
        assert [c.value for c in choices] == [_Color.RED, _Color.BLUE]


class TestAskOrCancel:
    def test_returns_value(self):
        assert wizard.ask_or_cancel("hello") == "hello"

    def test_raises_on_none(self):
        with pytest.raises(KeyboardInterrupt):
            wizard.ask_or_cancel(None)


class TestFindSmdFiles:
    def test_returns_sorted_smd_paths(self, tmp_path: Path):
        (tmp_path / "b.smd").write_text("")
        (tmp_path / "a.smd").write_text("")
        (tmp_path / "c.txt").write_text("")
        assert [p.name for p in wizard.find_smd_files(tmp_path)] == ["a.smd", "b.smd"]


class TestExtractSpecIdFromSmd:
    def test_returns_id(self, tmp_path: Path):
        path = tmp_path / "auth.smd"
        path.write_text(VALID_SMD)
        assert wizard.extract_spec_id_from_smd(path) == "AUTH"

    def test_returns_none_for_malformed(self, tmp_path: Path):
        path = tmp_path / "bad.smd"
        path.write_text("no frontmatter here")
        assert wizard.extract_spec_id_from_smd(path) is None

    def test_returns_none_for_empty_id(self, tmp_path: Path):
        path = tmp_path / "empty.smd"
        path.write_text(VALID_SMD.replace("id: AUTH", 'id: ""'))
        assert wizard.extract_spec_id_from_smd(path) is None


class TestGetAvailableSpecs:
    def test_skips_unparseable(self, tmp_path: Path):
        (tmp_path / "good.smd").write_text(VALID_SMD)
        (tmp_path / "bad.smd").write_text("garbage")
        assert wizard.get_available_specs(tmp_path) == [("AUTH", "good.smd")]


class TestPromptList:
    def test_collects_until_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.common.questionary") as mock_q:
            mock_q.text.return_value.ask.side_effect = ["one", "two", ""]
            assert wizard.prompt_list("Item", console) == ["one", "two"]

    def test_strips_whitespace(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.common.questionary") as mock_q:
            mock_q.text.return_value.ask.side_effect = ["  spaced  ", ""]
            assert wizard.prompt_list("Item", console) == ["spaced"]


class TestAskSpecId:
    def test_no_specs_returns_none(self, tmp_path: Path):
        console = MagicMock(spec=Console)
        assert wizard.ask_spec_id(tmp_path, console=console) is None
        console.print.assert_called()

    def test_selects_from_available(self, tmp_path: Path):
        (tmp_path / "auth.smd").write_text(VALID_SMD)
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.common.questionary") as mock_q:
            mock_q.select.return_value.ask.return_value = "AUTH"
            mock_q.Choice = MagicMock(side_effect=lambda title, value: value)
            assert wizard.ask_spec_id(tmp_path, console=console) == "AUTH"
