import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import make_config, make_plan, make_task

from ossature.build.builder import (
    _extract_commands_from_plan,
    _extract_executables,
    check_tool_availability,
    run_setup,
)


class TestRunSetup:
    def test_no_setup_returns_true(self, temp_dir: Path):
        config = make_config(temp_dir, language="rust")
        console = MagicMock()
        assert run_setup(config, console) is True

    def test_successful_setup(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        config = make_config(temp_dir, language="rust", setup="echo hello")
        console = MagicMock()
        assert run_setup(config, console) is True

    def test_failed_setup(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        config = make_config(temp_dir, language="rust", setup="false")
        console = MagicMock()
        assert run_setup(config, console) is False

    def test_setup_timeout(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        config = make_config(temp_dir, language="rust", setup="sleep 999")
        console = MagicMock()

        with patch(
            "ossature.build.builder.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sleep", 300),
        ):
            assert run_setup(config, console) is False

    def test_setup_runs_in_output_dir(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        config = make_config(temp_dir, language="rust", setup="pwd")
        console = MagicMock()

        with patch("ossature.build.builder.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="pwd", returncode=0, stdout="", stderr=""
            )
            run_setup(config, console)
            mock_run.assert_called_once()
            assert mock_run.call_args.kwargs["cwd"] == str(output_dir)


class TestExtractCommands:
    def test_collects_from_plan_and_config(self, temp_dir: Path):
        config = make_config(
            temp_dir, language="rust", setup="cargo init", verify="cargo check", test="cargo test"
        )
        plan = make_plan(
            [
                make_task("001", "TEST", verify="cargo check"),
                make_task("002", "TEST", verify="cargo test"),
            ]
        )
        commands = _extract_commands_from_plan(plan, config)
        assert "cargo init" in commands
        assert "cargo check" in commands
        assert "cargo test" in commands

    def test_empty_plan_and_config(self, temp_dir: Path):
        config = make_config(temp_dir, language="rust")
        plan = make_plan([])
        assert _extract_commands_from_plan(plan, config) == set()


class TestExtractExecutables:
    def test_simple_command(self):
        assert _extract_executables({"cargo check"}) == {"cargo"}

    def test_chained_commands(self):
        result = _extract_executables({"mkdir -p build && cmake .."})
        assert "mkdir" in result
        assert "cmake" in result

    def test_piped_commands(self):
        result = _extract_executables({"make 2>&1 | head"})
        assert "make" in result
        assert "head" in result

    def test_skips_builtins(self):
        result = _extract_executables({"cd build && cmake .."})
        assert "cd" not in result
        assert "cmake" in result

    def test_env_var_prefix(self):
        result = _extract_executables({"CC=gcc make"})
        assert "make" in result
        assert "CC=gcc" not in result

    def test_semicolons(self):
        result = _extract_executables({"cargo check; cargo test"})
        assert "cargo" in result

    def test_ignores_tokens_inside_quoted_strings(self):
        cmd = (
            "python -m mypy src/spenny/core.py --check-unused-ignore "
            '&& python -c "from spenny.core import add_expense; '
            "print('Core module imported successfully')\""
        )
        result = _extract_executables({cmd})
        assert result == {"python"}


class TestCheckToolAvailability:
    def test_all_tools_present(self, temp_dir: Path):
        config = make_config(temp_dir, language="rust")
        plan = make_plan([make_task("001", "TEST", verify="echo hello")])
        console = MagicMock()
        # echo is a builtin, so nothing to check
        assert check_tool_availability(plan, config, console) is True

    def test_missing_tool(self, temp_dir: Path):
        config = make_config(temp_dir, language="rust")
        plan = make_plan([make_task("001", "TEST", verify="nonexistent_tool_xyz check")])
        console = MagicMock()
        assert check_tool_availability(plan, config, console) is False

    def test_empty_plan(self, temp_dir: Path):
        config = make_config(temp_dir, language="rust")
        plan = make_plan([])
        console = MagicMock()
        assert check_tool_availability(plan, config, console) is True

    @patch("ossature.build.builder.shutil.which")
    def test_reports_all_missing(self, mock_which, temp_dir: Path):
        mock_which.return_value = None
        config = make_config(temp_dir, language="rust", setup="cargo init")
        plan = make_plan(
            [
                make_task("001", "TEST", verify="cargo check"),
                make_task("002", "TEST", verify="rustfmt src/main.rs"),
            ]
        )
        console = MagicMock()
        result = check_tool_availability(plan, config, console)
        assert result is False
