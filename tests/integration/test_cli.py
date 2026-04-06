import os
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from helpers import write_smd

from ossature.cli.main import cli
from ossature.templates.manager import TemplateLoader, TemplateResult


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

    def test_init_gitignore_matches_template(self, runner: CliRunner, temp_dir: Path):
        with runner.isolated_filesystem(temp_dir=temp_dir):
            runner.invoke(cli, ["init", "proj"])
            actual = (Path("proj") / ".gitignore").read_text()
            expected = TemplateLoader.get("gitignore")
            assert actual == expected

    def test_init_config_matches_template(self, runner: CliRunner, temp_dir: Path):
        with runner.isolated_filesystem(temp_dir=temp_dir):
            runner.invoke(cli, ["init", "proj"])
            actual = (Path("proj") / "ossature.toml").read_text()
            expected = TemplateLoader.get("config").format(name="proj")
            assert actual == expected

    def test_init_shows_errors(self, runner: CliRunner, temp_dir: Path):
        error_result = TemplateResult(created=[], skipped=[], errors=["Something broke"])
        with (
            runner.isolated_filesystem(temp_dir=temp_dir),
            patch(
                "ossature.cli.commands.init.TemplateManager.init_project",
                return_value=error_result,
            ),
        ):
            result = runner.invoke(cli, ["init", "fail-project"])

            assert result.exit_code == 1
            assert "Something broke" in result.output
            assert "Error" in result.output


class TestMutualExclusiveFlags:
    def _run(self, runner: CliRunner, project_dir: Path, args: list[str]):
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            return runner.invoke(cli, args)
        finally:
            os.chdir(old_cwd)

    def test_audit_interactive_and_no_fix(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Auth")
        result = self._run(runner, project_dir, ["audit", "--interactive", "--no-fix"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_retry_from_and_only(self, runner: CliRunner, project_dir: Path):
        result = self._run(runner, project_dir, ["retry", "--from", "001", "--only", "002"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_build_step_and_auto(self, runner: CliRunner, project_dir: Path):
        result = self._run(runner, project_dir, ["build", "--step", "--auto"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_build_skip_failures_without_auto(self, runner: CliRunner, project_dir: Path):
        result = self._run(runner, project_dir, ["build", "--skip-failures"])
        assert result.exit_code == 1
        assert "--auto" in result.output

    def test_audit_rejects_invalid_specs(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "A", "Module A", depends="NONEXISTENT")
        result = self._run(runner, project_dir, ["audit"])
        assert result.exit_code == 1
        assert "invalid" in result.output.lower()
