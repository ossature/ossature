from pathlib import Path

from ossature.build.commands import is_verify_command_error


class TestIsVerifyCommandError:
    """Detect command invocation errors vs code compilation errors."""

    def test_nim_arguments_error(self, tmp_path: Path) -> None:
        error = (
            "Hint: used config file '/opt/homebrew/Cellar/nim/2.2.8/nim/config/nim.cfg' [Conf]\n"
            "Hint: used config file "
            "'/opt/homebrew/Cellar/nim/2.2.8/nim/config/config.nims' [Conf]\n"
            "Error: arguments can only be given if the '--run' option is selected"
        )
        assert is_verify_command_error(error, tmp_path) is True

    def test_unknown_option(self, tmp_path: Path) -> None:
        error = "error: unknown option '--frobnicate'"
        assert is_verify_command_error(error, tmp_path) is True

    def test_unrecognized_command(self, tmp_path: Path) -> None:
        error = "error: no such subcommand: 'bild'"
        assert is_verify_command_error(error, tmp_path) is True

    def test_command_not_found(self, tmp_path: Path) -> None:
        error = "bash: nimc: command not found"
        assert is_verify_command_error(error, tmp_path) is True

    def test_code_compilation_error_not_misclassified(self, tmp_path: Path) -> None:
        error = (
            f"{tmp_path}/src/phantom/content.nim(28, 5) Error: "
            "identifier expected, but got 'keyword method'\n"
            f"{tmp_path}/src/phantom/content.nim(28, 5) Error: invalid indentation"
        )
        assert is_verify_command_error(error, tmp_path) is False

    def test_test_failure_not_misclassified(self, tmp_path: Path) -> None:
        error = f"{tmp_path}/tests/test_auth.py:42: AssertionError: expected True but got False"
        assert is_verify_command_error(error, tmp_path) is False

    def test_empty_output(self, tmp_path: Path) -> None:
        assert is_verify_command_error("", tmp_path) is False

    def test_hint_only_output(self, tmp_path: Path) -> None:
        error = "Hint: used config file '/some/path' [Conf]\nHint: all done [Conf]"
        assert is_verify_command_error(error, tmp_path) is False

    def test_usage_message(self, tmp_path: Path) -> None:
        error = "Usage: cargo [OPTIONS] [COMMAND]\n\nerror: unexpected argument '--chek'"
        assert is_verify_command_error(error, tmp_path) is True
