from unittest.mock import MagicMock, patch

import pytest

from ossature.cli.commands.audit import _confirm_or_abort


class TestConfirmOrAbort:
    def _patched_ask(self, answer):
        confirm = MagicMock()
        confirm.return_value.ask.return_value = answer
        return patch("ossature.cli.commands.audit.questionary.confirm", confirm)

    def test_yes_passes_through(self):
        with self._patched_ask(True):
            assert _confirm_or_abort("go?", default=False) is True

    def test_no_passes_through(self):
        with self._patched_ask(False):
            assert _confirm_or_abort("go?", default=True) is False

    def test_ctrl_c_aborts(self):
        with self._patched_ask(None), pytest.raises(SystemExit) as exc:
            _confirm_or_abort("go?", default=True)
        assert exc.value.code == 130
