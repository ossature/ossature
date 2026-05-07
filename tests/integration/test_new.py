from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from helpers import run_in_project, write_smd

from ossature.parsers.amd import parse_amd_file
from ossature.parsers.smd import parse_smd_file


class TestNewSmdCommand:
    def test_creates_smd_file(self, runner: CliRunner, project_dir: Path):
        result = run_in_project(runner, project_dir, ["new", "my-feature"])

        assert result.exit_code == 0
        assert (project_dir / "specs" / "my-feature.smd").exists()
        assert "Summary" in result.output

    def test_smd_file_has_correct_spec_id(self, runner: CliRunner, project_dir: Path):
        run_in_project(runner, project_dir, ["new", "my-feature"])

        content = (project_dir / "specs" / "my-feature.smd").read_text()
        assert "id: MY_FEATURE" in content

    def test_smd_file_is_parseable(self, runner: CliRunner, project_dir: Path):
        run_in_project(runner, project_dir, ["new", "my-feature"])

        spec = parse_smd_file(project_dir / "specs" / "my-feature.smd")
        assert spec.spec_id == "MY_FEATURE"
        assert spec.title == "My Feature"

    def test_smd_summary_shows_counts(self, runner: CliRunner, project_dir: Path):
        result = run_in_project(runner, project_dir, ["new", "my-feature"])

        assert "goal(s)" in result.output
        assert "requirement(s)" in result.output
        assert "constraint(s)" in result.output
        assert "example(s)" in result.output


class TestNewAmdCommand:
    def test_creates_amd_file(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            result = run_in_project(runner, project_dir, ["new", "my-arch", "-t", "amd"])

        assert result.exit_code == 0
        assert (project_dir / "specs" / "my-arch.amd").exists()

    def test_amd_file_is_parseable(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            run_in_project(runner, project_dir, ["new", "my-arch", "-t", "amd"])

        spec = parse_amd_file(project_dir / "specs" / "my-arch.amd")
        assert spec.spec_id == "AUTH"
        assert spec.title == "Architecture: My Arch"
