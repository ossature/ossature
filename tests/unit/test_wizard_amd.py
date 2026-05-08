from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from ossature.cli.wizard import amd as wizard
from ossature.models.amd import AMDSpec
from ossature.models.shared import Status

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


def _q_mock(answers):
    mock_q = MagicMock()
    iterator = iter(answers)

    def _ask(*_args, **_kwargs):
        prompt = MagicMock()
        prompt.ask.return_value = next(iterator)
        return prompt

    mock_q.text.side_effect = _ask
    mock_q.confirm.side_effect = _ask
    mock_q.select.side_effect = _ask
    return mock_q


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


class TestAskSpecId:
    def test_no_specs_returns_none(self, tmp_path: Path):
        console = MagicMock(spec=Console)
        assert wizard.ask_spec_id(tmp_path, console=console) is None
        console.print.assert_called()

    def test_selects_from_available(self, tmp_path: Path):
        (tmp_path / "auth.smd").write_text(VALID_SMD)
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary") as mock_q:
            mock_q.select.return_value.ask.return_value = "AUTH"
            mock_q.Choice = MagicMock(side_effect=lambda title, value: value)
            assert wizard.ask_spec_id(tmp_path, console=console) == "AUTH"


class TestPromptComponent:
    def test_collects_fields(self):
        console = MagicMock(spec=Console)
        answers = [
            "Storage",
            "src/storage.py",
            "persistence layer",
            "python",
            "def load() -> None: ...",
            "",
            "Other",
        ]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            component = wizard.prompt_component(console, 1)
        assert component.name == "Storage"
        assert component.path == "src/storage.py"
        assert component.interface == "def load() -> None: ..."
        assert component.depends_on == ["Other"]


class TestPromptComponents:
    def test_skipped_returns_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary", _q_mock([False])):
            assert wizard.prompt_components(console) == []

    def test_collects_one(self):
        console = MagicMock(spec=Console)
        answers = [
            True,  # add components?
            "Storage",
            "src/storage.py",
            "persistence",
            "python",
            "iface",
            "",
            "",  # no dependencies
            False,  # another?
        ]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            components = wizard.prompt_components(console)
        assert len(components) == 1
        assert components[0].depends_on == []


class TestPromptDataModels:
    def test_skipped_returns_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary", _q_mock([False])):
            assert wizard.prompt_data_models(console) == []

    def test_collects_one(self):
        console = MagicMock(spec=Console)
        answers = [
            True,
            "Bookmark",
            "python",
            "class Bookmark: ...",
            "",
            False,
        ]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            models = wizard.prompt_data_models(console)
        assert len(models) == 1
        assert models[0].name == "Bookmark"
        assert models[0].definition == "class Bookmark: ..."


class TestPromptDependencies:
    def test_skipped_returns_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary", _q_mock([False])):
            assert wizard.prompt_dependencies(console) == []

    def test_collects_one(self):
        console = MagicMock(spec=Console)
        answers = [True, "rusqlite", "sqlite bindings", False]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            deps = wizard.prompt_dependencies(console)
        assert len(deps) == 1
        assert deps[0].name == "rusqlite"
        assert deps[0].purpose == "sqlite bindings"


class TestPromptFlow:
    def test_skipped_returns_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary", _q_mock([False])):
            assert wizard.prompt_flow(console) == ""

    def test_collects_lines(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary", _q_mock([True, "step a", "step b", ""])):
            assert wizard.prompt_flow(console) == "step a\nstep b"


class TestPromptAmdSpec:
    def test_cancels_when_no_specs(self, tmp_path: Path):
        console = MagicMock(spec=Console)
        assert wizard.prompt_amd_spec("auth", tmp_path, console) is None

    def test_full_flow(self, tmp_path: Path):
        (tmp_path / "auth.smd").write_text(VALID_SMD)
        console = MagicMock(spec=Console)
        answers = [
            "AUTH",  # select spec
            "Auth System",  # title
            Status.DRAFT,  # status
            "Three modules.",  # overview
            False,  # add components?
            False,  # add data models?
            False,  # add flow?
            False,  # add dependencies?
            "",  # notes
        ]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            spec = wizard.prompt_amd_spec("auth", tmp_path, console)
        assert isinstance(spec, AMDSpec)
        assert spec.title == "Auth System"
        assert spec.spec_id == "AUTH"
        assert spec.status == Status.DRAFT
        assert spec.components == []
        assert spec.notes == ""

    def test_returns_none_on_cancel(self, tmp_path: Path):
        (tmp_path / "auth.smd").write_text(VALID_SMD)
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.amd.questionary") as mock_q:
            mock_q.select.return_value.ask.return_value = None
            mock_q.Choice = MagicMock(side_effect=lambda title, value: value)
            assert wizard.prompt_amd_spec("auth", tmp_path, console) is None
