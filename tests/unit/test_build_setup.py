import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import make_config, make_plan, make_task

from ossature.build.builder import (
    DefaultBuildBackend,
    _command_groups_from_plan,
    _extract_executables_for_group,
    _format_verify_for_display,
    _print_task_header,
    _prompt_after_failure,
    _split_tokens,
    check_tool_availability,
    final_output_paths,
    run_setup,
    run_verify,
)
from ossature.models.plan import PlanTask


class TestFinalOutputPaths:
    def test_excludes_paths_a_later_task_rewrites(self):
        plan = make_plan(
            [
                make_task("001", "S", outputs=["src/a.rs", "src/b.rs"]),
                make_task("002", "S", outputs=["src/a.rs"]),
            ]
        )
        scaffold, finalizer = plan.tasks[0], plan.tasks[1]
        # a.rs is rewritten by 002, so it is not 001's to finalize; b.rs is.
        assert final_output_paths(scaffold, plan) == ["src/b.rs"]
        # 002 is the last producer of a.rs.
        assert final_output_paths(finalizer, plan) == ["src/a.rs"]


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

    def test_setup_list_runs_each_step(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        config = make_config(
            temp_dir,
            language="rust",
            setup=["echo step-1", "echo step-2", "echo step-3"],
        )
        console = MagicMock()

        with patch("ossature.build.builder.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="echo", returncode=0, stdout="", stderr=""
            )
            assert run_setup(config, console) is True
            assert mock_run.call_count == 3

    def test_setup_list_stops_at_first_failure(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        config = make_config(temp_dir, language="rust", setup=["echo first", "false", "echo never"])
        console = MagicMock()

        results = [
            subprocess.CompletedProcess(args="echo first", returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args="false", returncode=1, stdout="", stderr=""),
        ]
        with patch("ossature.build.builder.subprocess.run", side_effect=results) as mock_run:
            assert run_setup(config, console) is False
            assert mock_run.call_count == 2


class TestRunVerify:
    def test_no_commands_returns_success(self, temp_dir: Path):
        assert run_verify([], temp_dir) == (True, "")

    def test_single_command_success_returns_stripped_stdout(self, temp_dir: Path):
        ok, output = run_verify(["echo hello"], temp_dir)
        assert ok is True
        assert output == "hello"

    def test_single_command_success_no_output(self, temp_dir: Path):
        ok, output = run_verify(["true"], temp_dir)
        assert ok is True
        assert output == ""

    def test_single_command_failure_returns_output(self, temp_dir: Path):
        ok, output = run_verify(["sh -c 'echo boom >&2; exit 1'"], temp_dir)
        assert ok is False
        assert "boom" in output

    def test_multi_step_success_includes_headers(self, temp_dir: Path):
        ok, output = run_verify(["echo first", "echo second"], temp_dir)
        assert ok is True
        assert "$ echo first" in output
        assert "first" in output
        assert "$ echo second" in output
        assert "second" in output

    def test_multi_step_includes_header_for_silent_step(self, temp_dir: Path):
        ok, output = run_verify(["true", "echo done"], temp_dir)
        assert ok is True
        # First command produces no output but its header still appears
        assert "$ true" in output
        assert "$ echo done" in output
        assert "done" in output

    def test_multi_step_fail_fast_stops_on_first_failure(self, temp_dir: Path):
        ok, output = run_verify(["echo step-1", "false", "echo never-runs"], temp_dir)
        assert ok is False
        assert "step-1" in output
        assert "never-runs" not in output

    def test_combines_stdout_and_stderr(self, temp_dir: Path):
        ok, output = run_verify(["sh -c 'echo out; echo err >&2'"], temp_dir)
        assert ok is True
        assert "out" in output
        assert "err" in output

    def test_runs_in_specified_cwd(self, temp_dir: Path):
        ok, output = run_verify(["pwd"], temp_dir)
        assert ok is True
        assert str(temp_dir.resolve()) in str(Path(output).resolve())

    def test_timeout_returns_failure(self, temp_dir: Path):
        with patch(
            "ossature.build.builder.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sleep", 120),
        ):
            ok, output = run_verify(["sleep 999"], temp_dir)
            assert ok is False
            assert "timed out" in output


class TestDefaultBuildBackendVerify:
    def test_delegates_to_run_verify(self, temp_dir: Path):
        config = make_config(temp_dir, language="python")
        backend = DefaultBuildBackend(config)
        ok, output = backend.verify(["echo hello"], temp_dir)
        assert ok is True
        assert output == "hello"

    def test_no_commands_returns_success(self, temp_dir: Path):
        config = make_config(temp_dir, language="python")
        backend = DefaultBuildBackend(config)
        assert backend.verify([], temp_dir) == (True, "")


class TestDefaultBuildBackendGenerate:
    def test_returns_agent_output(self, temp_dir: Path):
        config = make_config(temp_dir, language="python")
        backend = DefaultBuildBackend(config)

        fake_result = MagicMock()
        fake_result.output = "generated source"

        with (
            patch("ossature.build.builder._create_impl_agent") as mock_create,
            patch("ossature.build.builder._run_with_retry", return_value=fake_result),
        ):
            output = backend.generate(
                "build prompt", MagicMock(), MagicMock(), MagicMock(), "test-model"
            )
            assert output == "generated source"
            mock_create.assert_called_once_with(config)


class TestDefaultBuildBackendFix:
    def test_returns_agent_output(self, temp_dir: Path):
        config = make_config(temp_dir, language="python")
        backend = DefaultBuildBackend(config)

        fake_result = MagicMock()
        fake_result.output = "patched source"

        with (
            patch("ossature.build.builder._create_fix_agent") as mock_create,
            patch("ossature.build.builder._run_with_retry", return_value=fake_result),
        ):
            output = backend.fix("fix prompt", MagicMock(), MagicMock(), MagicMock(), "test-model")
            assert output == "patched source"
            mock_create.assert_called_once_with(config)


class TestPrintTaskHeader:
    def _task(self, **overrides) -> PlanTask:
        defaults = {
            "id": "042",
            "spec": "CORE",
            "title": "Build the thing",
            "description": "Construct widgets",
            "outputs": ["src/widget.py", "src/lib.py"],
            "depends_on": [],
            "spec_refs": [],
            "arch_refs": [],
            "verify": [],
        }
        defaults.update(overrides)
        return PlanTask(**defaults)

    def test_silent_when_not_verbose(self):
        console = MagicMock()
        _print_task_header(console, self._task(), total=99, verbose=False)
        console.print.assert_not_called()

    def test_verbose_emits_header_description_and_outputs(self):
        console = MagicMock()
        _print_task_header(console, self._task(), total=99, verbose=True)

        printed = " ".join(str(c.args[0]) if c.args else "" for c in console.print.call_args_list)
        assert "042/099" in printed
        assert "Build the thing" in printed
        assert "Construct widgets" in printed
        assert "src/widget.py" in printed
        assert "src/lib.py" in printed

    def test_verbose_omits_outputs_line_when_empty(self):
        console = MagicMock()
        _print_task_header(console, self._task(outputs=[]), total=10, verbose=True)

        printed = " ".join(str(c.args[0]) if c.args else "" for c in console.print.call_args_list)
        # The outputs line is the only one that uses the `->` arrow
        assert "->" not in printed


class TestPromptAfterFailure:
    def _task(self) -> PlanTask:
        return PlanTask(
            id="001",
            spec="CORE",
            title="t",
            description="d",
            outputs=[],
            depends_on=[],
            spec_refs=[],
            arch_refs=[],
            verify=[],
        )

    def test_retry_response(self):
        with patch("builtins.input", return_value="r"):
            assert _prompt_after_failure(MagicMock(), self._task()) == "retry"

    def test_skip_response(self):
        with patch("builtins.input", return_value="s"):
            assert _prompt_after_failure(MagicMock(), self._task()) == "skip"

    def test_unknown_response_quits(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_after_failure(MagicMock(), self._task()) == "quit"

    def test_response_is_case_insensitive(self):
        with patch("builtins.input", return_value="R"):
            assert _prompt_after_failure(MagicMock(), self._task()) == "retry"

    def test_eof_quits(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _prompt_after_failure(MagicMock(), self._task()) == "quit"

    def test_keyboard_interrupt_quits(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert _prompt_after_failure(MagicMock(), self._task()) == "quit"


class TestFormatVerifyForDisplay:
    def test_empty_list_returns_empty_string(self):
        assert _format_verify_for_display([]) == ""

    def test_single_command_returned_as_is(self):
        assert _format_verify_for_display(["cargo check"]) == "cargo check"

    def test_multiple_commands_joined_with_and(self):
        assert _format_verify_for_display(["make", "./app --help"]) == "make && ./app --help"


class TestCommandGroupsFromPlan:
    def test_collects_per_scope_groups(self, temp_dir: Path):
        config = make_config(
            temp_dir,
            language="rust",
            setup="cargo init",
            verify="cargo check",
        )
        plan = make_plan(
            [
                make_task("001", "TEST", verify=["cargo check", "cargo test"]),
                make_task("002", "TEST", verify="cargo build"),
            ]
        )
        groups = _command_groups_from_plan(plan, config)
        # 2 build-config groups + 2 task verify groups
        assert ["cargo init"] in groups
        assert ["cargo check"] in groups
        assert ["cargo check", "cargo test"] in groups
        assert ["cargo build"] in groups
        assert len(groups) == 4

    def test_empty_plan_and_config(self, temp_dir: Path):
        config = make_config(temp_dir, language="rust")
        plan = make_plan([])
        assert _command_groups_from_plan(plan, config) == []


class TestSplitTokens:
    def test_simple(self):
        assert _split_tokens("cargo check") == ["cargo", "check"]

    def test_quoted(self):
        assert _split_tokens('echo "hello world"') == ["echo", "hello world"]

    def test_malformed_quoting_falls_back(self):
        assert _split_tokens("echo 'unbalanced") == ["echo", "'unbalanced"]


class TestExtractExecutablesForGroup:
    def test_simple_command(self):
        assert set(_extract_executables_for_group(["cargo check"])) == {"cargo"}

    def test_skips_builtins(self):
        exes = _extract_executables_for_group(["cd build && cmake .."])
        assert "cd" not in exes
        assert "cmake" in exes

    def test_env_var_prefix(self):
        assert set(_extract_executables_for_group(["CC=gcc make"])) == {"make"}

    def test_chained_in_single_string(self):
        assert set(_extract_executables_for_group(["mkdir -p build && cmake .."])) == {
            "mkdir",
            "cmake",
        }

    def test_compile_then_run_chained_in_single_string(self):
        # Bug repro: /tmp/yep_test contains a slash so the shell never
        # consults PATH — it's invoked by direct file path.
        exes = _extract_executables_for_group(
            ["gcc -Wall -o /tmp/yep_test yep.c && /tmp/yep_test --help > /dev/null"]
        )
        assert set(exes) == {"gcc"}

    def test_compile_then_run_as_list(self):
        exes = _extract_executables_for_group(
            ["gcc -o /tmp/yep_test yep.c", "/tmp/yep_test --help"]
        )
        assert set(exes) == {"gcc"}

    def test_other_missing_tool_still_caught(self):
        # `other_missing_tool` is bare (no slash) → still flagged.
        exes = _extract_executables_for_group(["gcc -o /tmp/a a.c && /tmp/a && other_missing_tool"])
        assert set(exes) == {"gcc", "other_missing_tool"}

    def test_path_based_invocation_never_flagged(self):
        # Generic: any token containing `/` bypasses PATH and is treated
        # as a project artifact — works for any language/build system.
        assert set(_extract_executables_for_group(["./yep --help"])) == set()
        assert set(_extract_executables_for_group(["target/release/myapp"])) == set()
        assert set(_extract_executables_for_group(["zig-out/bin/x --version"])) == set()
        assert set(_extract_executables_for_group(["build/Release/foo"])) == set()
        assert set(_extract_executables_for_group(["node_modules/.bin/eslint"])) == set()
        assert set(_extract_executables_for_group(["/opt/bin/foo --version"])) == set()

    def test_make_then_run_compiled_binary(self):
        # `make` produces ./yep via Makefile; ./yep contains a slash so we
        # never need to know that `make` is what produced it.
        exes = _extract_executables_for_group(
            [
                "make clean",
                "make CFLAGS='-std=c99 -Wall -Wextra -pedantic'",
                "./yep --help > /tmp/yep_help.txt",
                "grep -q -- '--help' /tmp/yep_help.txt",
                "./yep --version > /tmp/yep_version.txt",
            ]
        )
        assert set(exes) == {"make", "grep"}

    def test_cargo_then_run_release_binary(self):
        exes = _extract_executables_for_group(
            ["cargo build --release", "target/release/myapp --version"]
        )
        assert set(exes) == {"cargo"}

    def test_zig_build_then_run(self):
        exes = _extract_executables_for_group(["zig build", "zig-out/bin/myapp --help"])
        assert set(exes) == {"zig"}

    def test_go_build_then_run(self):
        exes = _extract_executables_for_group(["go build -o bin/app ./...", "bin/app --help"])
        assert set(exes) == {"go"}

    def test_npm_then_run_local_bin(self):
        exes = _extract_executables_for_group(["npm install", "node_modules/.bin/jest"])
        assert set(exes) == {"npm"}


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

    @patch("ossature.build.builder.shutil.which")
    def test_compile_then_run_list_form_does_not_flag_produced_binary(
        self, mock_which, temp_dir: Path
    ):
        # Pretend gcc is on PATH, but /tmp/yep_test obviously isn't
        mock_which.side_effect = lambda exe: "/usr/bin/gcc" if exe == "gcc" else None
        config = make_config(temp_dir, language="rust")
        plan = make_plan(
            [
                make_task(
                    "001",
                    "YEP",
                    verify=[
                        "gcc -Wall -Wextra -std=c99 -o /tmp/yep_test yep.c",
                        "/tmp/yep_test --help > /dev/null",
                    ],
                )
            ]
        )
        console = MagicMock()
        assert check_tool_availability(plan, config, console) is True

    @patch("ossature.build.builder.shutil.which")
    def test_compile_then_run_chained_string_form_does_not_flag_produced_binary(
        self, mock_which, temp_dir: Path
    ):
        # Same scenario but verify is one chained shell string (back-compat).
        mock_which.side_effect = lambda exe: "/usr/bin/gcc" if exe == "gcc" else None
        config = make_config(temp_dir, language="rust")
        plan = make_plan(
            [
                make_task(
                    "001",
                    "YEP",
                    verify=(
                        "gcc -Wall -Wextra -std=c99 -o /tmp/yep_test yep.c "
                        "&& /tmp/yep_test --help > /dev/null"
                    ),
                )
            ]
        )
        console = MagicMock()
        assert check_tool_availability(plan, config, console) is True

    @patch("ossature.build.builder.shutil.which")
    def test_bare_name_without_slash_is_flagged(self, mock_which, temp_dir: Path):
        # `myprog` (no slash) is treated as a PATH lookup. Without `./`,
        # the shell wouldn't actually find it in cwd either, so flagging
        # is the right behavior — and tells the user to fix their verify
        # command to use a path.
        def _which(exe: str) -> str | None:
            return "/usr/bin/gcc" if exe == "gcc" else None

        mock_which.side_effect = _which
        config = make_config(temp_dir, language="c")
        plan = make_plan(
            [
                make_task("001", "A", verify=["gcc -o myprog x.c", "myprog --help"]),
            ]
        )
        console = MagicMock()
        assert check_tool_availability(plan, config, console) is False

    @patch("ossature.build.builder.shutil.which")
    def test_yep_project_real_world_plan(self, mock_which, temp_dir: Path):
        # Mirrors /Users/beshr/src/code/yep/.ossature/plan.toml — the
        # original failing case. `make` produces ./yep via the Makefile;
        # ./yep is invoked by direct path and must not be flagged.
        def _which(exe: str) -> str | None:
            if exe in {"make", "grep", "sh"}:
                return f"/usr/bin/{exe}"
            return None

        mock_which.side_effect = _which
        config = make_config(temp_dir, language="c")
        plan = make_plan(
            [
                make_task("001", "YEP", verify=["make -n yep", "make -n clean"]),
                make_task(
                    "002",
                    "YEP",
                    verify=[
                        "make clean",
                        "make CFLAGS='-std=c99 -Wall -Wextra -pedantic'",
                        "./yep --help > /tmp/yep_help.txt",
                        "grep -q -- '--help' /tmp/yep_help.txt",
                        "./yep --version > /tmp/yep_version.txt",
                    ],
                ),
                make_task(
                    "003",
                    "YEP",
                    verify=[
                        "make clean",
                        "make CFLAGS='-std=c99 -Wall -Wextra -pedantic'",
                        "sh tests/test_yep.sh",
                    ],
                ),
            ]
        )
        console = MagicMock()
        assert check_tool_availability(plan, config, console) is True
