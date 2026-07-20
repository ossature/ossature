from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestPromptComponent:
    def test_collects_fields(self):
        console = MagicMock(spec=Console)
        answers = [
            "Storage",
            "src/storage.py",
            "persistence layer",
            "python",
            "def load() -> None: ...",
            "",  # end of interface lines
            "load returns empty data when the file is missing",
            "",  # end of contracts
            "Other",
        ]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            component = wizard.prompt_component(console, 1)
        assert component.name == "Storage"
        assert component.path == "src/storage.py"
        assert component.interface == "def load() -> None: ..."
        assert component.contracts == ["load returns empty data when the file is missing"]
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
            "",  # end of interface lines
            "",  # no contracts
            "",  # no dependencies
            False,  # another?
        ]
        with patch("ossature.cli.wizard.amd.questionary", _q_mock(answers)):
            components = wizard.prompt_components(console)
        assert len(components) == 1
        assert components[0].contracts == []
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
        # ask_spec_id (which consumes the first answer) lives in wizard.common,
        # the rest of the prompts in wizard.amd; both go through the shared mock.
        mock_q = _q_mock(answers)
        with (
            patch("ossature.cli.wizard.amd.questionary", mock_q),
            patch("ossature.cli.wizard.common.questionary", mock_q),
        ):
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
        mock_q = MagicMock()
        mock_q.select.return_value.ask.return_value = None
        mock_q.Choice = MagicMock(side_effect=lambda title, value: value)
        with (
            patch("ossature.cli.wizard.amd.questionary", mock_q),
            patch("ossature.cli.wizard.common.questionary", mock_q),
        ):
            assert wizard.prompt_amd_spec("auth", tmp_path, console) is None
