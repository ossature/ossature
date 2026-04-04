import os
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from ossature.cli.main import cli


class TestCleanCommand:
    def _run(self, runner: CliRunner, project_dir: Path, args: list[str] | None = None):
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            return runner.invoke(cli, args or ["clean"])
        finally:
            os.chdir(old_cwd)

    def test_nothing_to_clean(self, runner: CliRunner, project_dir: Path):
        result = self._run(runner, project_dir)
        assert result.exit_code == 0
        assert "Nothing to clean" in result.output

    def test_removes_ossature_dir(self, runner: CliRunner, project_dir: Path):
        ossature_dir = project_dir / ".ossature"
        ossature_dir.mkdir()
        (ossature_dir / "plan.toml").write_text("data")

        with patch("ossature.cli.commands.clean.questionary") as mock_q:
            mock_q.confirm.return_value.ask.return_value = True
            result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert not ossature_dir.exists()
        assert "reset complete" in result.output

    def test_decline_confirmation_exits(self, runner: CliRunner, project_dir: Path):
        ossature_dir = project_dir / ".ossature"
        ossature_dir.mkdir()

        with patch("ossature.cli.commands.clean.questionary") as mock_q:
            mock_q.confirm.return_value.ask.return_value = False
            result = self._run(runner, project_dir)

        assert result.exit_code == 0
        assert ossature_dir.exists()
