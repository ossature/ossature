from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from ossature.cli.main import cli
from ossature.templates.manager import TemplateResult


class TestInitCmd:
    def test_init_with_name_creates_project(self, runner: CliRunner, temp_dir: Path):
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(cli, ["init", "test-project"])

            assert result.exit_code == 0
            assert (Path("test-project") / "ossature.toml").exists()
            assert (Path("test-project") / ".gitignore").exists()
            assert (Path("test-project") / "specs").is_dir()
            assert (Path("test-project") / "context").is_dir()
            assert "Success" in result.output

    def test_init_dot_uses_current_dir(self, runner: CliRunner, temp_dir: Path):
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(cli, ["init"])

            assert result.exit_code == 0
            assert Path("ossature.toml").exists()
            assert Path(".gitignore").exists()

    def test_init_skips_existing_files(self, runner: CliRunner, temp_dir: Path):
        with runner.isolated_filesystem(temp_dir=temp_dir):
            runner.invoke(cli, ["init", "myproject"])
            result = runner.invoke(cli, ["init", "myproject"])

            assert result.exit_code == 0
            assert "Skipped" in result.output

    def test_init_shows_errors(self, runner: CliRunner, temp_dir: Path):
        error_result = TemplateResult(created=[], skipped=[], errors=["Something broke"])
        with runner.isolated_filesystem(temp_dir=temp_dir):
            with patch(
                "ossature.cli.commands.init.TemplateManager.init_project",
                return_value=error_result,
            ):
                result = runner.invoke(cli, ["init", "fail-project"])

                assert result.exit_code == 1
                assert "Something broke" in result.output
                assert "Error" in result.output
