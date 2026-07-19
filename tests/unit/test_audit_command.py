from unittest.mock import MagicMock, patch

import pytest
from conftest import make_config

from ossature.cli.commands.audit import _AuditRun, _confirm_or_abort


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


class TestAuditRunInternals:
    def _make_run(self, tmp_path):
        config = make_config(tmp_path)
        return _AuditRun(
            config, MagicMock(), "auto", replan=False, interactive=False, errors_ok=False
        )

    def test_fix_cycle_rejects_negative_max_cycles(self, tmp_path):
        run = self._make_run(tmp_path)
        run.config.audit.max_fix_cycles = -1
        with pytest.raises(RuntimeError, match="Unreachable"):
            run._run_fix_cycle(
                MagicMock(),
                status_text=lambda cycle: "",
                audit_once=MagicMock(),
                log_label="x",
                title="x",
                confirm_text=lambda n: "",
                fix_once=MagicMock(),
                fixing_status="",
                fixed_label="x",
                no_edits_message="",
                on_fixed=MagicMock(),
            )

    def test_write_report_skips_without_reports(self, tmp_path):
        run = self._make_run(tmp_path)
        run._write_report()
        run.console.log.assert_not_called()
