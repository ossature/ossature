from enum import Enum
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from ossature.cli.wizard import smd as wizard
from ossature.models.shared import Status
from ossature.models.smd import Priority, SMDSpec


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


class TestPromptList:
    def test_collects_until_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(["one", "two", ""])):
            assert wizard.prompt_list("Item", console) == ["one", "two"]

    def test_strips_whitespace(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(["  spaced  ", ""])):
            assert wizard.prompt_list("Item", console) == ["spaced"]


class TestPromptError:
    def test_returns_condition_and_response(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(["bad input", "show error"])):
            assert wizard.prompt_error(console, 1) == ("bad input", "show error")


class TestPromptErrors:
    def test_skipped_returns_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.smd.questionary", _q_mock([False])):
            assert wizard.prompt_errors(console) == []

    def test_collects_one(self):
        console = MagicMock(spec=Console)
        answers = [True, "bad", "fail", False]
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(answers)):
            assert wizard.prompt_errors(console) == [("bad", "fail")]


class TestPromptRequirement:
    def test_collects_fields_without_errors(self):
        console = MagicMock(spec=Console)
        answers = [
            "Add Item",  # title
            "Adds an item.",  # description
            "name (string)",  # accepts
            "the new id",  # returns
            False,  # add error cases?
        ]
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(answers)):
            req = wizard.prompt_requirement(console, 1)
        assert req.title == "Add Item"
        assert req.description == "Adds an item."
        assert req.accepts == "name (string)"
        assert req.returns == "the new id"
        assert req.errors == []


class TestPromptRequirements:
    def test_collects_one(self):
        console = MagicMock(spec=Console)
        answers = [
            "Title",
            "Desc",
            "Accepts",
            "Returns",
            False,  # add error cases?
            False,  # add another requirement?
        ]
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(answers)):
            reqs = wizard.prompt_requirements(console)
        assert len(reqs) == 1
        assert reqs[0].title == "Title"


class TestPromptExample:
    def test_collects_input_and_output(self):
        console = MagicMock(spec=Console)
        answers = ["Sample", "input1", "input2", "", "output1", ""]
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(answers)):
            example = wizard.prompt_example(console, 1)
        assert example.name == "Sample"
        assert example.input == "input1\ninput2"
        assert example.output == "output1"


class TestPromptExamples:
    def test_skipped_returns_empty(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.smd.questionary", _q_mock([False])):
            assert wizard.prompt_examples(console) == []

    def test_collects_one(self):
        console = MagicMock(spec=Console)
        answers = [
            True,  # add examples?
            "Sample",
            "in",
            "",
            "out",
            "",
            False,  # another?
        ]
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(answers)):
            examples = wizard.prompt_examples(console)
        assert len(examples) == 1
        assert examples[0].name == "Sample"


class TestPromptSmdSpec:
    def test_full_flow(self):
        console = MagicMock(spec=Console)
        answers = [
            "Auth Module",  # title
            "AUTH",  # spec id
            Status.DRAFT,  # status
            Priority.HIGH,  # priority
            "DB, USER",  # depends
            "An overview.",  # overview
            "Goal one",
            "",  # goals end
            "Non goal one",
            "",  # non-goals end
            # requirement
            "Login",
            "Authenticate user.",
            "username, password",
            "session token",
            False,  # add errors
            False,  # add another requirement
            "Constraint A",
            "",  # constraints end
            False,  # add examples?
            "Done when login works",
            "",  # acceptance criteria end
            "Some notes",  # notes
        ]
        with patch("ossature.cli.wizard.smd.questionary", _q_mock(answers)):
            spec = wizard.prompt_smd_spec("auth", console)
        assert isinstance(spec, SMDSpec)
        assert spec.title == "Auth Module"
        assert spec.spec_id == "AUTH"
        assert spec.status == Status.DRAFT
        assert spec.priority == Priority.HIGH
        assert spec.depends == ["DB", "USER"]
        assert spec.overview == "An overview."
        assert spec.goals == ["Goal one"]
        assert spec.non_goals == ["Non goal one"]
        assert len(spec.requirements) == 1
        assert spec.constraints == ["Constraint A"]
        assert spec.examples == []
        assert spec.acceptance_criteria == ["Done when login works"]
        assert spec.notes == "Some notes"

    def test_returns_none_on_cancel(self):
        console = MagicMock(spec=Console)
        with patch("ossature.cli.wizard.smd.questionary") as mock_q:
            mock_q.text.return_value.ask.return_value = None
            assert wizard.prompt_smd_spec("auth", console) is None
