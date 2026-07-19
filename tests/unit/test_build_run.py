from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_config, make_plan, make_task
from pydantic_ai.exceptions import AgentRunError

from ossature.build.builder import BuildMode, _BuildRun, execute_build
from ossature.build.state import TaskState, compute_input_hash
from ossature.build.task import TaskResult
from ossature.models.plan import TaskStatus


def _make_run(
    tmp_path: Path,
    tasks: list,
    mode: BuildMode = BuildMode.DEFAULT,
) -> _BuildRun:
    config = make_config(tmp_path)
    config.metadata_path.mkdir(parents=True, exist_ok=True)
    plan = make_plan(tasks)
    return _BuildRun(config, plan, {}, {}, MagicMock(), tmp_path / "plan.toml", mode, verbose=False)


def _logged(run: _BuildRun) -> str:
    return " ".join(str(c) for c in run.console.log.call_args_list)


class TestRunOneSkips:
    def test_manual_task_skipped(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S", status=TaskStatus.MANUAL)])
        assert run._run_one(run.plan.tasks[0], MagicMock()) is True
        assert "MANUAL" in _logged(run)

    def test_skip_next_consumes_flag(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        run.skip_next = True
        assert run._run_one(run.plan.tasks[0], MagicMock()) is True
        assert run.skip_next is False
        assert run.plan.tasks[0].status == TaskStatus.SKIPPED

    def test_unmet_deps_stop_default_mode(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("002", "S", depends_on=["001"])])
        assert run._run_one(run.plan.tasks[0], MagicMock()) is False
        assert run.stopped is True
        assert run.plan.tasks[0].status == TaskStatus.FAILED
        assert "Dependencies not met" in _logged(run)

    def test_unmet_deps_continue_in_auto_skip(self, tmp_path: Path) -> None:
        run = _make_run(
            tmp_path, [make_task("002", "S", depends_on=["001"])], mode=BuildMode.AUTO_SKIP
        )
        assert run._run_one(run.plan.tasks[0], MagicMock()) is True
        assert run.stopped is False


class TestCachedTaskStands:
    def test_modified_output_marks_pending(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S", outputs=["x.py"], status=TaskStatus.DONE)])
        task = run.plan.tasks[0]
        prompt = run._assemble_prompt(task)
        run.state.set(
            task.id,
            TaskState(
                input_hash=compute_input_hash(prompt, task, run.config),
                output_hash="sha256:not-what-is-on-disk",
                created_files=["x.py"],
            ),
        )
        assert run._cached_task_stands(task, MagicMock()) is False
        assert task.status == TaskStatus.PENDING
        assert "output modified" in _logged(run)


class TestMaybeExtractInterface:
    def test_waits_for_all_spec_tasks_done(self, tmp_path: Path) -> None:
        run = _make_run(
            tmp_path,
            [
                make_task("001", "S"),
                make_task("002", "S", status=TaskStatus.DONE),
            ],
        )
        run.rebuilt_specs.add("S")
        with patch("ossature.build.builder.extract_spec_interface") as extract:
            run._maybe_extract_interface(run.plan.tasks[1], MagicMock())
        extract.assert_not_called()


class TestStepMode:
    def _successful_run(self, tmp_path: Path) -> _BuildRun:
        return _make_run(tmp_path, [make_task("001", "S")], mode=BuildMode.STEP)

    def test_quit_after_success_stops(self, tmp_path: Path) -> None:
        run = self._successful_run(tmp_path)
        with (
            patch(
                "ossature.build.builder._run_task_dispatch",
                return_value=TaskResult(success=True),
            ),
            patch("ossature.build.builder._prompt_after_success", return_value="quit"),
        ):
            assert run._run_one(run.plan.tasks[0], MagicMock()) is False
        assert run.stopped is True

    def test_skip_after_success_sets_skip_next(self, tmp_path: Path) -> None:
        run = self._successful_run(tmp_path)
        with (
            patch(
                "ossature.build.builder._run_task_dispatch",
                return_value=TaskResult(success=True),
            ),
            patch("ossature.build.builder._prompt_after_success", return_value="skip"),
        ):
            assert run._run_one(run.plan.tasks[0], MagicMock()) is True
        assert run.skip_next is True


class TestDispatchWithRecovery:
    def test_llm_error_auto_skip_continues(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")], mode=BuildMode.AUTO_SKIP)
        with patch("ossature.build.builder._run_task_dispatch", side_effect=AgentRunError("boom")):
            assert run._dispatch_with_recovery(run.plan.tasks[0], "p", MagicMock()) is None
        assert run.stopped is False
        assert "continuing" in _logged(run)

    def test_llm_error_auto_stops(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")], mode=BuildMode.AUTO)
        with patch("ossature.build.builder._run_task_dispatch", side_effect=AgentRunError("boom")):
            assert run._dispatch_with_recovery(run.plan.tasks[0], "p", MagicMock()) is None
        assert run.stopped is True

    def test_llm_error_retry_then_success(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        result = TaskResult(success=True)
        with (
            patch(
                "ossature.build.builder._run_task_dispatch",
                side_effect=[AgentRunError("boom"), result],
            ),
            patch("ossature.build.builder._prompt_after_failure", return_value="retry"),
        ):
            assert run._dispatch_with_recovery(run.plan.tasks[0], "p", MagicMock()) is result
        assert run.stopped is False

    def test_llm_error_skip_marks_task(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with (
            patch("ossature.build.builder._run_task_dispatch", side_effect=AgentRunError("boom")),
            patch("ossature.build.builder._prompt_after_failure", return_value="skip"),
        ):
            assert run._dispatch_with_recovery(run.plan.tasks[0], "p", MagicMock()) is None
        assert run.plan.tasks[0].status == TaskStatus.SKIPPED
        assert run.stopped is False

    def test_llm_error_quit_stops(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with (
            patch("ossature.build.builder._run_task_dispatch", side_effect=AgentRunError("boom")),
            patch("ossature.build.builder._prompt_after_failure", return_value="quit"),
        ):
            assert run._dispatch_with_recovery(run.plan.tasks[0], "p", MagicMock()) is None
        assert run.stopped is True


class TestHandleFailure:
    def test_auto_skip_continues(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")], mode=BuildMode.AUTO_SKIP)
        assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is True
        assert "failed, continuing" in _logged(run)

    def test_auto_stops(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")], mode=BuildMode.AUTO)
        assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is False
        assert run.stopped is True

    def test_interactive_skip(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with patch("ossature.build.builder._prompt_after_failure", return_value="skip"):
            assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is True
        assert run.plan.tasks[0].status == TaskStatus.SKIPPED

    def test_interactive_quit(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with patch("ossature.build.builder._prompt_after_failure", return_value="quit"):
            assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is False
        assert run.stopped is True

    def test_retry_llm_error_stops(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with (
            patch("ossature.build.builder._prompt_after_failure", return_value="retry"),
            patch("ossature.build.builder._run_task_dispatch", side_effect=AgentRunError("boom")),
        ):
            assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is False
        assert run.stopped is True
        assert run.plan.tasks[0].status == TaskStatus.FAILED

    def test_retry_success_records_task(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with (
            patch("ossature.build.builder._prompt_after_failure", return_value="retry"),
            patch(
                "ossature.build.builder._run_task_dispatch",
                return_value=TaskResult(success=True),
            ),
        ):
            assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is True
        assert run.plan.tasks[0].status == TaskStatus.DONE
        assert "(retry)" in _logged(run)

    def test_retry_still_failing_stops(self, tmp_path: Path) -> None:
        run = _make_run(tmp_path, [make_task("001", "S")])
        with (
            patch("ossature.build.builder._prompt_after_failure", return_value="retry"),
            patch(
                "ossature.build.builder._run_task_dispatch",
                return_value=TaskResult(success=False),
            ),
        ):
            assert run._handle_failure(run.plan.tasks[0], "p", MagicMock()) is False
        assert run.stopped is True


class TestPrintSummary:
    def test_counts_failed_and_skipped(self, tmp_path: Path) -> None:
        run = _make_run(
            tmp_path,
            [
                make_task("001", "S", status=TaskStatus.DONE),
                make_task("002", "S", status=TaskStatus.FAILED),
                make_task("003", "S", status=TaskStatus.SKIPPED),
            ],
        )
        run._print_summary()
        panel = run.console.print.call_args_list[-1][0][0]
        text = panel.renderable.plain
        assert "Done: 1/3" in text
        assert "Failed: 1" in text
        assert "Skipped: 1" in text


class TestExecuteBuildPreflight:
    def test_missing_tools_exit(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        plan = make_plan([make_task("001", "S")])
        with (
            patch("ossature.build.builder.check_tool_availability", return_value=False),
            pytest.raises(SystemExit),
        ):
            execute_build(config, plan, {}, {}, MagicMock(), tmp_path / "plan.toml")

    def test_failed_setup_exits(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        config.metadata_path.mkdir(parents=True, exist_ok=True)
        plan = make_plan([make_task("001", "S")])
        with (
            patch("ossature.build.builder.check_tool_availability", return_value=True),
            patch("ossature.build.builder.run_setup", return_value=False),
            pytest.raises(SystemExit),
        ):
            execute_build(config, plan, {}, {}, MagicMock(), tmp_path / "plan.toml")
