from unittest.mock import MagicMock, patch

from rich.console import Console

from ossature.cli.wizard import vmd as wizard
from ossature.models.shared import Status


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


class TestPromptVmdSpec:
    def test_full_flow_value_group(self, tmp_path):
        answers = [
            Status.DRAFT,  # status
            "value",  # group type
            "parse_duration",  # function under test
            "text",  # parameter names
            "compact",  # case name
            '"2h30m"',  # input for text
            "9000",  # expected
            True,  # add another case?
            "empty",  # case name
            '""',  # input for text
            "!ValueError: empty",  # expected
            False,  # add another case?
            False,  # add another group?
        ]
        console = MagicMock(spec=Console)

        with (
            patch("ossature.cli.wizard.vmd.questionary", _q_mock(answers)),
            patch("ossature.cli.wizard.vmd.ask_spec_id", return_value="RELATIVE_TIME"),
        ):
            spec = wizard.prompt_vmd_spec("relative-checks", tmp_path, console)

        assert spec is not None
        assert spec.spec_id == "RELATIVE_TIME"
        group = spec.groups[0]
        assert group.name == "parse_duration"
        assert [p.name for p in group.params] == ["text"]
        assert [c.name for c in group.cases] == ["compact", "empty"]
        assert group.cases[0].inputs == ["2h30m"]
        assert group.cases[0].expected == 9000
        assert group.cases[1].expect_kind == "error"
        assert group.cases[1].error_message == "empty"

    def test_full_flow_command_scenario(self, tmp_path):
        answers = [
            Status.DRAFT,  # status
            "command",  # kind
            "rejects bad flags",  # scenario name
            "yep --nope",  # command
            "2",  # exit code
            "",  # stdout contains (skip)
            "usage",  # stderr contains
            False,  # add another step?
            False,  # add another group or scenario?
        ]
        console = MagicMock(spec=Console)

        with (
            patch("ossature.cli.wizard.vmd.questionary", _q_mock(answers)),
            patch("ossature.cli.wizard.vmd.ask_spec_id", return_value="YEP"),
        ):
            spec = wizard.prompt_vmd_spec("yep-checks", tmp_path, console)

        assert spec is not None
        scenario = spec.scenarios[0]
        assert scenario.kind == "command"
        step = scenario.steps[0]
        assert step.argv == ["yep", "--nope"]
        assert step.exit_code == 2
        assert step.stderr_mode == "has"
        assert step.stderr == "usage"

    def test_unparseable_input_returns_none(self, tmp_path):
        answers = [
            Status.DRAFT,
            "value",
            "f",
            "x",
            "case_1",
            "not json",  # invalid input cell
            "9000",
            False,
            False,
        ]
        console = MagicMock(spec=Console)

        with (
            patch("ossature.cli.wizard.vmd.questionary", _q_mock(answers)),
            patch("ossature.cli.wizard.vmd.ask_spec_id", return_value="S"),
        ):
            spec = wizard.prompt_vmd_spec("checks", tmp_path, console)

        assert spec is None

    def test_no_spec_id_returns_none(self, tmp_path):
        console = MagicMock(spec=Console)

        with patch("ossature.cli.wizard.vmd.ask_spec_id", return_value=None):
            spec = wizard.prompt_vmd_spec("checks", tmp_path, console)

        assert spec is None

    def test_returns_none_on_cancel(self, tmp_path):
        answers = [None]  # cancelling the status prompt
        console = MagicMock(spec=Console)

        with (
            patch("ossature.cli.wizard.vmd.questionary", _q_mock(answers)),
            patch("ossature.cli.wizard.vmd.ask_spec_id", return_value="S"),
        ):
            spec = wizard.prompt_vmd_spec("checks", tmp_path, console)

        assert spec is None
