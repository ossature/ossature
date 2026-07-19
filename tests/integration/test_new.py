from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from helpers import run_in_project, write_smd

from ossature.parsers.amd import parse_amd_file
from ossature.parsers.smd import parse_smd_file
from ossature.parsers.vmd import parse_vmd, parse_vmd_file


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

    def test_existing_file_fails_cleanly(self, runner: CliRunner, project_dir: Path):
        run_in_project(runner, project_dir, ["new", "my-feature"])
        result = run_in_project(runner, project_dir, ["new", "my-feature"])

        assert result.exit_code == 1
        assert "already exists" in result.output
        assert "Traceback" not in result.output


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
        assert spec.title == "My Arch"


class TestNewVmdCommand:
    def test_creates_vmd_file(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            result = run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd"])

        assert result.exit_code == 0
        assert (project_dir / "specs" / "auth-checks.vmd").exists()
        assert "Summary" in result.output

    def test_vmd_file_is_parseable(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd"])

        spec = parse_vmd_file(project_dir / "specs" / "auth-checks.vmd")
        assert spec.spec_id == "AUTH"
        assert [g.name for g in spec.groups] == ["primary_action"]
        assert len(spec.groups[0].cases) == 2

    def test_vmd_template_validates(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd"])

        result = run_in_project(runner, project_dir, ["validate"])
        assert result.exit_code == 0
        assert "VMD" in result.output

    def test_vmd_without_specs_exits(self, runner: CliRunner, project_dir: Path):
        result = run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd"])

        assert result.exit_code == 0
        assert not (project_dir / "specs" / "auth-checks.vmd").exists()
        assert "No specification files found" in result.output
        assert "A verification file" in result.output

    def test_vmd_summary_shows_counts(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            result = run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd"])

        assert "group(s)" in result.output
        assert "case(s)" in result.output


class TestNewVmdInteractive:
    def test_interactive_saves_wizard_result(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")
        wizard_spec = parse_vmd('@spec AUTH\n\ncore_requirement(x)\nbasic | 1 | "ok"\n')

        with patch("ossature.cli.commands.new.prompt_vmd_spec", return_value=wizard_spec):
            result = run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd", "-i"])

        assert result.exit_code == 0
        saved = parse_vmd_file(project_dir / "specs" / "auth-checks.vmd")
        assert saved == wizard_spec

    def test_interactive_cancel_writes_nothing(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")

        with patch("ossature.cli.commands.new.prompt_vmd_spec", return_value=None):
            result = run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd", "-i"])

        assert result.exit_code == 0
        assert not (project_dir / "specs" / "auth-checks.vmd").exists()

    def test_existing_file_is_not_overwritten(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, spec_id="AUTH", title="Auth")
        existing = project_dir / "specs" / "auth-checks.vmd"
        existing.write_text("@spec AUTH\n\nf(x)\na | 1 | 2\n")

        with patch("ossature.cli.commands.new.ask_spec_id", return_value="AUTH"):
            result = run_in_project(runner, project_dir, ["new", "auth-checks", "-t", "vmd"])

        assert result.exit_code == 1
        assert "already exists" in result.output
        assert existing.read_text().startswith("@spec AUTH")
