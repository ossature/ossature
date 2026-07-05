from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic_ai import ModelRetry
from rich.console import Console

from ossature.build.builder import (
    BuildContext,
    _register_tools,
    _resolve_sandboxed,
    _validate_command,
)
from ossature.shared import FileEdit, apply_edits


@pytest.fixture
def quiet_console() -> Console:
    return Console(quiet=True)


class TestResolveSandboxed:
    def test_simple_relative_path(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, "src/main.rs", quiet_console)
        assert result == tmp_path / "src" / "main.rs"

    def test_nested_relative_path(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, "src/auth/mod.rs", quiet_console)
        assert result == tmp_path / "src" / "auth" / "mod.rs"

    def test_dot_in_filename(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, "Cargo.toml", quiet_console)
        assert result == tmp_path / "Cargo.toml"

    def test_rejects_parent_traversal(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "../etc/passwd", quiet_console)

    def test_rejects_deep_traversal(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "src/../../etc/shadow", quiet_console)

    def test_rejects_absolute_path(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "/etc/passwd", quiet_console)

    def test_rejects_absolute_path_to_different_dir(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "/tmp/evil", quiet_console)

    def test_allows_current_dir(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, ".", quiet_console)
        assert result == tmp_path

    def test_allows_path_with_dot_segments_staying_inside(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        result = _resolve_sandboxed(tmp_path, "src/../src/main.rs", quiet_console)
        assert result == tmp_path / "src" / "main.rs"

    def test_rejects_traversal_via_dot_segments(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "src/../../outside", quiet_console)


class TestValidateCommand:
    def test_allows_simple_command(self, tmp_path: Path, quiet_console: Console) -> None:
        _validate_command("cargo check", tmp_path, quiet_console)

    def test_allows_make(self, tmp_path: Path, quiet_console: Console) -> None:
        _validate_command("make build", tmp_path, quiet_console)

    def test_allows_relative_path(self, tmp_path: Path, quiet_console: Console) -> None:
        _validate_command("./build.sh", tmp_path, quiet_console)

    def test_rejects_traversal(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cat ../secret", tmp_path, quiet_console)

    def test_rejects_absolute_path(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("/bin/rm -rf /", tmp_path, quiet_console)

    def test_rejects_chained_absolute(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("echo hello; /usr/bin/evil", tmp_path, quiet_console)

    def test_rejects_pipe_to_absolute(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("echo hello | /usr/bin/evil", tmp_path, quiet_console)

    def test_rejects_and_absolute(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("true && /usr/bin/evil", tmp_path, quiet_console)

    def test_rejects_traversal_in_middle(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cat src/../../etc/passwd", tmp_path, quiet_console)

    def test_allows_pytest(self, tmp_path: Path, quiet_console: Console) -> None:
        _validate_command("python -m pytest tests/", tmp_path, quiet_console)

    def test_allows_cargo_test(self, tmp_path: Path, quiet_console: Console) -> None:
        _validate_command("cargo test --release", tmp_path, quiet_console)

    def test_rejects_ls_root(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("ls /", tmp_path, quiet_console)

    def test_rejects_ls_absolute_dir(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("ls /Users", tmp_path, quiet_console)

    def test_rejects_cat_absolute_arg(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cat /etc/passwd", tmp_path, quiet_console)

    def test_rejects_cp_absolute_dest(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cp foo.txt /tmp/foo.txt", tmp_path, quiet_console)

    def test_allows_absolute_path_inside_output_dir(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        _validate_command(f'grep -r "Foo" {tmp_path} --include="*.py"', tmp_path, quiet_console)

    def test_allows_absolute_subdir_inside_output_dir(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        _validate_command(f"cat {tmp_path}/src/main.py", tmp_path, quiet_console)

    def test_rejects_shell_variable(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="shell expansions"):
            _validate_command("cat $HOME/secret", tmp_path, quiet_console)

    def test_rejects_command_substitution(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="shell expansions"):
            _validate_command("cat $(pwd)/../secret", tmp_path, quiet_console)

    def test_rejects_backtick_substitution(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="shell expansions"):
            _validate_command("cat `pwd`/../secret", tmp_path, quiet_console)


class TestApplyEdits:
    SAMPLE = 'fn main() {\n    println!("hello");\n}\n'

    def test_single_edit(self) -> None:
        edits = [FileEdit(old="hello", new="world")]
        result = apply_edits(self.SAMPLE, edits)
        assert "world" in result
        assert "hello" not in result

    def test_multiple_edits(self) -> None:
        content = "aaa\nbbb\nccc\n"
        edits = [FileEdit(old="aaa", new="AAA"), FileEdit(old="ccc", new="CCC")]
        result = apply_edits(content, edits)
        assert result == "AAA\nbbb\nCCC\n"

    def test_sequential_edits_see_previous_changes(self) -> None:
        content = "foo bar"
        edits = [FileEdit(old="foo", new="baz"), FileEdit(old="baz bar", new="done")]
        result = apply_edits(content, edits)
        assert result == "done"

    def test_multiline_old_and_new(self) -> None:
        content = "start\n    if x > 0 {\n        return x;\n    }\nend\n"
        edits = [
            FileEdit(
                old="    if x > 0 {\n        return x;\n    }",
                new="    if x > 0 {\n        return x * 2;\n    }",
            )
        ]
        result = apply_edits(content, edits)
        assert "return x * 2;" in result

    def test_rejects_empty_array(self) -> None:
        with pytest.raises(ModelRetry, match="empty"):
            apply_edits("content", [])

    def test_rejects_identical_old_new(self) -> None:
        with pytest.raises(ModelRetry, match="identical"):
            apply_edits("content", [FileEdit(old="x", new="x")])

    def test_rejects_old_not_found(self) -> None:
        with pytest.raises(ModelRetry, match="not found"):
            apply_edits("hello world", [FileEdit(old="missing", new="x")])

    def test_rejects_ambiguous_match(self) -> None:
        with pytest.raises(ModelRetry, match="matches 2 locations"):
            apply_edits("aaa bbb aaa", [FileEdit(old="aaa", new="x")])


class _ToolRecorder:
    """Stands in for an Agent so the registered tool closures can be called
    directly, without a model run."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, fn):
        self.tools[fn.__name__] = fn
        return fn


def _tool_ctx(tmp_path: Path, protected_paths: list[str] | None = None):
    return SimpleNamespace(
        deps=BuildContext(
            output_dir=tmp_path,
            console=MagicMock(),
            status=MagicMock(),
            protected_paths=protected_paths or [],
        )
    )


@pytest.fixture
def tools():
    recorder = _ToolRecorder()
    _register_tools(recorder)  # type: ignore[arg-type]
    return recorder.tools


class TestFileToolWriteProtection:
    def test_write_file_creates_and_tracks(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)

        result = tools["write_file"](ctx, "src/main.py", "print('hi')\n")

        assert "Written" in result
        assert (tmp_path / "src" / "main.py").read_text() == "print('hi')\n"
        assert ctx.deps.created_files == ["src/main.py"]

    def test_write_file_rejects_fixtures_dir(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)

        with pytest.raises(ModelRetry, match="read-only"):
            tools["write_file"](ctx, "checks/x.cases.json", "{}")

    def test_write_file_rejects_protected_path(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path, protected_paths=["tests/test_checks_f.py"])

        with pytest.raises(ModelRetry, match="read-only"):
            tools["write_file"](ctx, "tests/test_checks_f.py", "cheat")

    def test_edit_file_edits_and_tracks(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("value = 1\n")

        result = tools["edit_file"](ctx, "src/app.py", [FileEdit(old="value = 1", new="value = 2")])

        assert "Edited" in result
        assert (tmp_path / "src" / "app.py").read_text() == "value = 2\n"
        assert ctx.deps.edited_files == ["src/app.py"]

    def test_edit_file_rejects_fixtures_dir(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        (tmp_path / "checks").mkdir()
        (tmp_path / "checks" / "f.cases.json").write_text("{}")

        with pytest.raises(ModelRetry, match="read-only"):
            tools["edit_file"](ctx, "checks/f.cases.json", [FileEdit(old="{}", new="[]")])
