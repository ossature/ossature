import os
from pathlib import Path

from click.testing import CliRunner
from helpers import run_in_project, write_smd

from ossature.cli.commands.validate import _detect_cycle
from ossature.cli.main import cli

MINIMAL_AMD = """\
# Architecture: {title}

@spec: {spec_id}
@status: draft

## Overview

Some overview text.

## Components

### ComponentName

@path: src/component.py

Component description.

**Interface:**

```python
def do_something() -> None: ...
```
"""


class TestValidateCommand:
    def test_validates_single_smd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "Validated" in result.output

    def test_validates_smd_with_amd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_path.write_text(MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH"))

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "Validated" in result.output
        assert "1" in result.output  # 1 SMD
        assert "AMD" in result.output

    def test_no_spec_files_prints_warning(self, runner: CliRunner, project_dir: Path):
        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "No spec files" in result.output

    def test_invalid_smd_exits_with_error(self, runner: CliRunner, project_dir: Path):
        (project_dir / "specs" / "broken.smd").write_text("not valid")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "error" in result.output.lower()
        finally:
            os.chdir(old_cwd)

    def test_invalid_amd_exits_with_error(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.amd").write_text("not valid amd")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "error" in result.output.lower()
        finally:
            os.chdir(old_cwd)

    def test_missing_dependency_exits_with_error(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module", depends="NONEXISTENT")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "doesn't exist" in result.output
        finally:
            os.chdir(old_cwd)

    def test_amd_referencing_nonexistent_spec(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "ghost.amd"
        amd_path.write_text(MINIMAL_AMD.format(title="Ghost Architecture", spec_id="GHOST"))

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "doesn't exist" in result.output
        finally:
            os.chdir(old_cwd)

    def test_multi_spec_with_valid_depends(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        write_smd(project_dir, "API", "API Module", depends="AUTH")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "Validated" in result.output

    def test_verbose_output(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")

        result = run_in_project(runner, project_dir, ["-v", "validate"])

        assert result.exit_code == 0
        assert "Validating" in result.output

    def test_verbose_with_amd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_path.write_text(MINIMAL_AMD.format(title="Auth Arch", spec_id="AUTH"))

        result = run_in_project(runner, project_dir, ["-v", "validate"])

        assert result.exit_code == 0
        assert "Validating 1 AMD" in result.output
        assert "✓" in result.output

    def test_verbose_invalid_smd_shows_details(self, runner: CliRunner, project_dir: Path):
        (project_dir / "specs" / "broken.smd").write_text("not valid")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["-v", "validate"])
            assert result.exit_code == 1
            assert "error(s)" in result.output
        finally:
            os.chdir(old_cwd)

    def test_verbose_invalid_amd_shows_details(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Auth")
        (project_dir / "specs" / "auth.amd").write_text("not valid")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["-v", "validate"])
            assert result.exit_code == 1
            assert "error(s)" in result.output
        finally:
            os.chdir(old_cwd)

    def test_verbose_missing_dep_exits_with_error(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Auth", depends="NONEXISTENT")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["-v", "validate"])
            assert result.exit_code == 1
            assert "doesn't exist" in result.output
        finally:
            os.chdir(old_cwd)

    def test_circular_dependency_two_specs(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "A", "Module A", depends="B")
        write_smd(project_dir, "B", "Module B", depends="A")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "Circular dependency" in result.output
            assert "A" in result.output
            assert "B" in result.output
        finally:
            os.chdir(old_cwd)

    def test_circular_dependency_three_specs(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "A", "Module A", depends="B")
        write_smd(project_dir, "B", "Module B", depends="C")
        write_smd(project_dir, "C", "Module C", depends="A")

        old_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "Circular dependency" in result.output
        finally:
            os.chdir(old_cwd)

    def test_no_cycle_passes(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "A", "Module A")
        write_smd(project_dir, "B", "Module B", depends="A")
        write_smd(project_dir, "C", "Module C", depends="A, B")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "All checks passed" in result.output

    def test_config_not_found_exits_with_error(self, runner: CliRunner, temp_dir: Path):
        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 1
            assert "Error" in result.output
        finally:
            os.chdir(old_cwd)


class TestDetectCycle:
    def test_no_deps(self):
        assert _detect_cycle({"A": [], "B": []}) is None

    def test_linear_chain(self):
        assert _detect_cycle({"A": [], "B": ["A"], "C": ["B"]}) is None

    def test_two_node_cycle(self):
        result = _detect_cycle({"A": ["B"], "B": ["A"]})
        assert result is not None
        assert "A" in result
        assert "B" in result

    def test_three_node_cycle(self):
        result = _detect_cycle({"A": ["B"], "B": ["C"], "C": ["A"]})
        assert result is not None
        assert len(result) == 3

    def test_self_cycle(self):
        result = _detect_cycle({"A": ["A"]})
        assert result is not None
        assert "A" in result

    def test_cycle_with_uninvolved_specs(self):
        result = _detect_cycle({"X": [], "A": ["B"], "B": ["A"], "Y": ["X"]})
        assert result is not None
        assert "X" not in result

    def test_empty_graph(self):
        assert _detect_cycle({}) is None

    def test_dep_not_in_graph(self):
        assert _detect_cycle({"A": ["EXTERNAL"]}) is None
