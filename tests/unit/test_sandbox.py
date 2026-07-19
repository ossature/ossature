import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import ModelRetry
from rich.console import Console

from ossature.build.tools import (
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


def _tool_ctx(
    tmp_path: Path,
    protected_paths: list[str] | None = None,
    context_dir: Path | None = None,
    verbose: bool = False,
):
    return SimpleNamespace(
        deps=BuildContext(
            output_dir=tmp_path,
            console=MagicMock(),
            status=MagicMock(),
            verbose=verbose,
            context_dir=context_dir,
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


class TestValidateCommandParsing:
    def test_rejects_unparseable_quoting(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="could not be parsed"):
            _validate_command('echo "unclosed', tmp_path, quiet_console)


class TestBuildContextLogging:
    def test_log_tool_prints_when_verbose(self, tmp_path: Path) -> None:
        ctx = _tool_ctx(tmp_path, verbose=True)
        ctx.deps.log_tool("hello")
        ctx.deps.console.log.assert_called_once_with("hello")

    def test_log_tool_silent_by_default(self, tmp_path: Path) -> None:
        ctx = _tool_ctx(tmp_path)
        ctx.deps.log_tool("hello")
        ctx.deps.console.log.assert_not_called()


class TestWriteAndEditToolBranches:
    def test_write_file_second_write_is_update(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        tools["write_file"](ctx, "src/a.py", "x = 1\n")
        result = tools["write_file"](ctx, "src/a.py", "x = 2\n")
        assert "Written" in result
        assert ctx.deps.created_files == ["src/a.py"]

    def test_write_file_os_error_reported(self, tmp_path: Path, tools) -> None:
        # Parent path exists as a file, so mkdir fails
        (tmp_path / "src").write_text("a file, not a directory")
        ctx = _tool_ctx(tmp_path)
        result = tools["write_file"](ctx, "src/a.py", "x = 1\n")
        assert result.startswith("Error writing")

    def test_edit_file_missing_file_raises(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        with pytest.raises(ModelRetry, match="does not exist"):
            tools["edit_file"](ctx, "src/missing.py", [FileEdit(old="a", new="b")])

    def test_edit_file_read_error_reported(self, tmp_path: Path, tools) -> None:
        (tmp_path / "adir").mkdir()
        ctx = _tool_ctx(tmp_path)
        result = tools["edit_file"](ctx, "adir", [FileEdit(old="a", new="b")])
        assert result.startswith("Error reading")


class TestReadTools:
    def test_read_file_returns_content(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("content here")
        ctx = _tool_ctx(tmp_path)
        assert tools["read_file"](ctx, "f.txt") == "content here"

    def test_read_file_missing(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        assert "does not exist" in tools["read_file"](ctx, "nope.txt")

    def test_read_file_os_error(self, tmp_path: Path, tools) -> None:
        (tmp_path / "adir").mkdir()
        ctx = _tool_ctx(tmp_path)
        assert tools["read_file"](ctx, "adir").startswith("Error reading")

    def test_read_lines_numbered_slice(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("one\ntwo\nthree\nfour\n")
        ctx = _tool_ctx(tmp_path)
        result = tools["read_lines"](ctx, "f.txt", 2, 3)
        assert "Lines 2-3 of 4" in result
        assert "2: two" in result
        assert "3: three" in result
        assert "one" not in result

    def test_read_lines_clamps_range(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("one\ntwo\n")
        ctx = _tool_ctx(tmp_path)
        result = tools["read_lines"](ctx, "f.txt", 0, 99)
        assert "Lines 1-2 of 2" in result

    def test_read_lines_missing(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        assert "does not exist" in tools["read_lines"](ctx, "nope.txt", 1, 2)

    def test_grep_file_matches_with_context(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("alpha\nbeta\ngamma\n")
        ctx = _tool_ctx(tmp_path)
        result = tools["grep_file"](ctx, "f.txt", "beta")
        assert "> 2: beta" in result
        assert "1: alpha" in result
        assert "3: gamma" in result

    def test_grep_file_no_match(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("alpha\n")
        ctx = _tool_ctx(tmp_path)
        assert "No matches" in tools["grep_file"](ctx, "f.txt", "zeta")

    def test_grep_file_invalid_pattern(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("alpha\n")
        ctx = _tool_ctx(tmp_path)
        assert "Invalid pattern" in tools["grep_file"](ctx, "f.txt", "(")

    def test_grep_file_missing(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        assert "does not exist" in tools["grep_file"](ctx, "nope.txt", "x")

    def test_list_files_entries_and_sizes(self, tmp_path: Path, tools) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.txt").write_text("abc")
        ctx = _tool_ctx(tmp_path)
        result = tools["list_files"](ctx, ".")
        assert "sub/" in result
        assert "a.txt (3 bytes)" in result

    def test_list_files_not_a_directory(self, tmp_path: Path, tools) -> None:
        (tmp_path / "f.txt").write_text("x")
        ctx = _tool_ctx(tmp_path)
        assert "is not a directory" in tools["list_files"](ctx, "f.txt")

    def test_list_files_empty(self, tmp_path: Path, tools) -> None:
        (tmp_path / "empty").mkdir()
        ctx = _tool_ctx(tmp_path)
        assert "is empty" in tools["list_files"](ctx, "empty")

    def test_list_files_truncates_long_listings(self, tmp_path: Path, tools) -> None:
        big = tmp_path / "big"
        big.mkdir()
        for i in range(201):
            (big / f"f{i:03d}.txt").write_text("")
        ctx = _tool_ctx(tmp_path)
        assert "more entries (truncated)" in tools["list_files"](ctx, "big")


class TestRunCommandTool:
    def test_reports_exit_code_and_output(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        result = tools["run_command"](ctx, "sh -c 'echo out; echo err >&2'")
        assert result.startswith("Exit code: 0")
        assert "out" in result
        assert "err" in result

    def test_nonzero_exit_code(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        result = tools["run_command"](ctx, "false")
        assert result.startswith("Exit code: 1")

    def test_timeout_reported(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        with patch(
            "ossature.build.tools.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sleep", 120),
        ):
            assert "timed out" in tools["run_command"](ctx, "sleep 999")


class TestContextTools:
    def test_copy_requires_context_dir(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        assert "no context directory" in tools["copy_context_file"](ctx, "a.mp3", "assets/a.mp3")

    def test_copy_rejects_escape(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        ctx = _tool_ctx(tmp_path / "out", context_dir=context)
        with pytest.raises(ModelRetry, match="outside the context directory"):
            tools["copy_context_file"](ctx, "../secret.txt", "dest.txt")

    def test_copy_missing_source(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        ctx = _tool_ctx(out, context_dir=context)
        assert "does not exist" in tools["copy_context_file"](ctx, "a.mp3", "assets/a.mp3")

    def test_copy_success_tracks_created(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        (context / "a.mp3").write_bytes(b"\x00\x01")
        out = tmp_path / "out"
        out.mkdir()
        ctx = _tool_ctx(out, context_dir=context)
        result = tools["copy_context_file"](ctx, "a.mp3", "assets/a.mp3")
        assert "Copied" in result
        assert (out / "assets" / "a.mp3").read_bytes() == b"\x00\x01"
        assert ctx.deps.created_files == ["assets/a.mp3"]
        tools["copy_context_file"](ctx, "a.mp3", "assets/a.mp3")
        assert ctx.deps.created_files == ["assets/a.mp3"]

    def test_copy_os_error_reported(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        (context / "a.txt").write_text("x")
        out = tmp_path / "out"
        out.mkdir()
        (out / "assets").write_text("a file, not a directory")
        ctx = _tool_ctx(out, context_dir=context)
        result = tools["copy_context_file"](ctx, "a.txt", "assets/a.txt")
        assert result.startswith("Error copying")

    def test_read_requires_context_dir(self, tmp_path: Path, tools) -> None:
        ctx = _tool_ctx(tmp_path)
        assert "no context directory" in tools["read_context_file"](ctx, "notes.txt")

    def test_read_rejects_escape(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        ctx = _tool_ctx(tmp_path / "out", context_dir=context)
        with pytest.raises(ModelRetry, match="outside the context directory"):
            tools["read_context_file"](ctx, "../secret.txt")

    def test_read_missing_source(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        ctx = _tool_ctx(tmp_path, context_dir=context)
        assert "does not exist" in tools["read_context_file"](ctx, "notes.txt")

    def test_read_success(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        (context / "notes.txt").write_text("remember this")
        ctx = _tool_ctx(tmp_path, context_dir=context)
        assert tools["read_context_file"](ctx, "notes.txt") == "remember this"

    def test_read_binary_reports_hint(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        context.mkdir()
        (context / "blob.bin").write_bytes(b"\xff\xfe\x00")
        ctx = _tool_ctx(tmp_path, context_dir=context)
        assert "binary file" in tools["read_context_file"](ctx, "blob.bin")

    def test_read_os_error_reported(self, tmp_path: Path, tools) -> None:
        context = tmp_path / "context"
        (context / "adir").mkdir(parents=True)
        ctx = _tool_ctx(tmp_path, context_dir=context)
        assert tools["read_context_file"](ctx, "adir").startswith("Error reading context file")


class TestToolErrorBranches:
    def test_edit_file_write_error_reported(self, tmp_path: Path, tools) -> None:
        target = tmp_path / "locked.py"
        target.write_text("value = 1\n")
        target.chmod(0o444)
        ctx = _tool_ctx(tmp_path)
        try:
            result = tools["edit_file"](
                ctx, "locked.py", [FileEdit(old="value = 1", new="value = 2")]
            )
        finally:
            target.chmod(0o644)
        assert result.startswith("Error writing")

    def test_read_lines_os_error(self, tmp_path: Path, tools) -> None:
        (tmp_path / "adir").mkdir()
        ctx = _tool_ctx(tmp_path)
        assert tools["read_lines"](ctx, "adir", 1, 2).startswith("Error reading")

    def test_grep_file_os_error(self, tmp_path: Path, tools) -> None:
        (tmp_path / "adir").mkdir()
        ctx = _tool_ctx(tmp_path)
        assert tools["grep_file"](ctx, "adir", "x").startswith("Error reading")

    def test_list_files_os_error(self, tmp_path: Path, tools) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        ctx = _tool_ctx(tmp_path)
        try:
            result = tools["list_files"](ctx, "locked")
        finally:
            locked.chmod(0o755)
        assert result.startswith("Error listing")
