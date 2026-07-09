import os
from pathlib import Path

from click.testing import CliRunner
from helpers import run_in_project, write_smd

from ossature.cli.commands.validate import _detect_cycle
from ossature.cli.main import cli

MINIMAL_AMD = """\
---
spec: {spec_id}
status: draft
---

# Architecture: {title}

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

**Contracts:** None
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

    def test_unknown_amd_section_warns(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_text = MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH")
        amd_text += "\n## Contracts:\n\n- Misplaced contract\n"
        amd_path.write_text(amd_text)

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "Unknown section" in result.output

    def test_unknown_amd_section_warns_verbose(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_text = MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH")
        amd_text += "\n## Custom Stuff\n\nSome text.\n"
        amd_path.write_text(amd_text)

        result = run_in_project(runner, project_dir, ["-v", "validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "Unknown section" in result.output

    def test_duplicate_component_across_amds_fails(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_a = MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH")
        amd_b = MINIMAL_AMD.format(title="Auth Models", spec_id="AUTH")
        (project_dir / "specs" / "auth.amd").write_text(amd_a)
        (project_dir / "specs" / "auth-models.amd").write_text(amd_b)

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 1
        assert "duplicate component name" in result.output

    def test_unknown_amd_section_with_markup_chars(self, runner: CliRunner, project_dir: Path):
        # Section names land in rich output; bracketed text must not be
        # parsed as markup (which would crash or swallow the heading).
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_text = MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH")
        amd_text += "\n## Routes [/api/v1]\n\nSome text.\n"
        amd_path.write_text(amd_text)

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "Routes" in result.output

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

    def test_complex_spec_warning(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "BIG", "Big Module", requirement_description="x" * 3100)

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "BIG" in result.output
        assert "high requirement complexity" in result.output
        assert "Consider splitting" in result.output

    def test_complex_spec_warning_verbose(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "BIG", "Big Module", requirement_description="x" * 3100)

        result = run_in_project(runner, project_dir, ["-v", "validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "high requirement complexity" in result.output

    def test_no_warning_at_threshold(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "OK", "Ok Module", requirement_description="x" * 2900)

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" not in result.output

    def test_no_warning_below_threshold(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "SMALL", "Small Module")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" not in result.output


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


MINIMAL_VMD = """\
@spec {spec_id}

{group}(input_data)
basic | "sample" | "processed"
"""


class TestValidateVMD:
    def test_validates_smd_with_vmd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        vmd_path = project_dir / "specs" / "auth.vmd"
        vmd_path.write_text(MINIMAL_VMD.format(spec_id="AUTH", group="core_requirement"))

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "VMD" in result.output

    def test_vmd_parse_error_fails(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        vmd_path = project_dir / "specs" / "auth.vmd"
        vmd_path.write_text("core_requirement(x)\nbasic | 1 | 2\n")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 1
        assert "@spec" in result.output

    def test_orphan_vmd_fails(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        vmd_path = project_dir / "specs" / "other.vmd"
        vmd_path.write_text(MINIMAL_VMD.format(spec_id="NOPE", group="core_requirement"))

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 1
        assert "NOPE" in result.output

    def test_duplicate_group_across_vmds_fails(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        for name in ("a.vmd", "b.vmd"):
            (project_dir / "specs" / name).write_text(
                MINIMAL_VMD.format(spec_id="AUTH", group="core_requirement")
            )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 1
        assert "duplicate verification group" in result.output

    def test_unresolved_target_warns_without_amd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        vmd_path = project_dir / "specs" / "auth.vmd"
        vmd_path.write_text(MINIMAL_VMD.format(spec_id="AUTH", group="unrelated_thing"))

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "unrelated_thing" in result.output

    def test_target_resolves_against_amd_interface(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_path.write_text(MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH"))
        vmd_path = project_dir / "specs" / "auth.vmd"
        vmd_path.write_text(MINIMAL_VMD.format(spec_id="AUTH", group="do_something"))

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "does not appear" not in result.output

    def test_missing_target_in_amd_interface_warns(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        amd_path = project_dir / "specs" / "auth.amd"
        amd_path.write_text(MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH"))
        vmd_path = project_dir / "specs" / "auth.vmd"
        vmd_path.write_text(MINIMAL_VMD.format(spec_id="AUTH", group="not_in_interface"))

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "does not appear" in result.output

    def test_verbose_lists_vmd_files(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        vmd_path = project_dir / "specs" / "auth.vmd"
        vmd_path.write_text(MINIMAL_VMD.format(spec_id="AUTH", group="core_requirement"))

        result = run_in_project(runner, project_dir, ["-v", "validate"])

        assert result.exit_code == 0
        assert "Validating 1 VMD(s)" in result.output


class TestValidateCoverage:
    def test_coverage_table_shown_with_vmd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            MINIMAL_VMD.format(spec_id="AUTH", group="core_requirement")
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "Requirement Coverage" in result.output
        assert "core_requirement" in result.output

    def test_no_coverage_table_without_vmd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "Requirement Coverage" not in result.output

    def test_uncovered_requirement_warns(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            MINIMAL_VMD.format(spec_id="AUTH", group="unrelated_thing")
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "no covering" in result.output

    def test_require_coverage_fails_validation(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            MINIMAL_VMD.format(spec_id="AUTH", group="unrelated_thing")
        )
        config_path = project_dir / "ossature.toml"
        config_path.write_text(config_path.read_text() + "\n[test]\nrequire_coverage = true\n")

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 1
        assert "no covering" in result.output


class TestValidateCoverageBranches:
    def test_exempt_requirement_shown(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        smd_path = project_dir / "specs" / "auth.smd"
        smd_path.write_text(
            smd_path.read_text().replace(
                "### Core Requirement", "### Core Requirement {.no-verify}"
            )
        )
        (project_dir / "specs" / "auth.vmd").write_text(
            MINIMAL_VMD.format(spec_id="AUTH", group="unrelated_helper")
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "exempt" in result.output
        assert "no covering" not in result.output

    def test_plan_tasks_feed_coverage(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            MINIMAL_VMD.format(spec_id="AUTH", group="unrelated_helper")
        )
        ossature_dir = project_dir / ".ossature"
        ossature_dir.mkdir()
        (ossature_dir / "plan.toml").write_text(
            '[meta]\ngenerated_at = "now"\ntotal_tasks = 1\nspecs = ["AUTH"]\n\n'
            "[[task]]\n"
            'id = "001"\nspec = "AUTH"\ntitle = "Golden test"\ndescription = ""\n'
            "outputs = []\ndepends_on = []\nspec_refs = []\narch_refs = []\n"
            'status = "pending"\nverify = []\ncovers = ["Core Requirement"]\n'
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "task:001" in result.output
        assert "no covering" not in result.output

    def test_outdated_plan_format_is_tolerated(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            MINIMAL_VMD.format(spec_id="AUTH", group="core_requirement")
        )
        ossature_dir = project_dir / ".ossature"
        ossature_dir.mkdir()
        (ossature_dir / "plan.toml").write_text(
            '[meta]\ngenerated_at = "now"\ntotal_tasks = 1\nspecs = ["AUTH"]\n\n'
            "[[task]]\n"
            'id = "001"\nspec = "AUTH"\ntitle = "Old"\ndescription = ""\n'
            "outputs = []\ndepends_on = []\n"
            'spec_refs = ["AUTH:overview"]\narch_refs = []\n'
            'status = "pending"\nverify = []\n'
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "Requirement Coverage" in result.output

    def test_verbose_vmd_parse_error_lists_details(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text("f(x)\na | nope | 2\n")

        result = run_in_project(runner, project_dir, ["-v", "validate"])

        assert result.exit_code == 1
        assert "Missing required directive: @spec" in result.output


class TestValidateVMDBranches:
    def test_dangling_arch_fails(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            "@spec AUTH\n@arch NOPE\n\ncore_requirement(x)\na | 1 | 2\n"
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 1
        assert "points @arch at" in result.output

    def test_command_scenarios_skip_amd_target_check(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.amd").write_text(
            MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH")
        )
        (project_dir / "specs" / "auth.vmd").write_text(
            '@spec AUTH\n\n@covers "Core Requirement"\nscenario runs the tool:\n'
            "when $ mytool x\nthen exit 0\n"
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "does not appear" not in result.output

    def test_call_scenario_target_checked_against_amd(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.amd").write_text(
            MINIMAL_AMD.format(title="Auth Architecture", spec_id="AUTH")
        )
        (project_dir / "specs" / "auth.vmd").write_text(
            '@spec AUTH\n\n@covers "Core Requirement"\nscenario calls a ghost:\n'
            "when ghost_function(1)\nthen ok\n"
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "does not appear" in result.output

    def test_error_coverage_fraction_shown(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        smd_path = project_dir / "specs" / "auth.smd"
        smd_path.write_text(
            smd_path.read_text().replace(
                "**Returns:** processed output",
                "**Returns:** processed output\n\n**Errors:**\n\n"
                "- Bad input -> raise ValueError with a message",
            )
        )
        (project_dir / "specs" / "auth.vmd").write_text(
            '@spec AUTH\n\ncore_requirement(x)\nbasic | 1 | "ok"\nbad | null | !ValueError\n'
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "1/1" in result.output

    def test_dangling_covers_target_warns(self, runner: CliRunner, project_dir: Path):
        write_smd(project_dir, "AUTH", "Authentication Module")
        (project_dir / "specs" / "auth.vmd").write_text(
            '@spec AUTH\n\n@covers nonexistent-thing\ncore_requirement(x)\nbasic | 1 | "ok"\n'
        )

        result = run_in_project(runner, project_dir, ["validate"])

        assert result.exit_code == 0
        assert "matches no requirement" in result.output
