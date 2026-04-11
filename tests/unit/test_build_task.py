from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from pydantic_ai.exceptions import AgentRunError

from ossature.build.builder import (
    BuildContext,
    build_task,
)
from ossature.models.plan import PlanTask
from ossature.shared.llm import UsageTracker


def _make_task(verify: str = "cargo test") -> PlanTask:
    return PlanTask(
        id="010",
        spec="CORE",
        title="Test task",
        description="Build something",
        outputs=["src/main.rs"],
        depends_on=[],
        spec_refs=[],
        arch_refs=[],
        verify=verify,
    )


def _make_config(tmp_path: Path) -> Any:
    config = MagicMock()
    config.output_path = tmp_path / "output"
    config.output_path.mkdir()
    config.metadata_path = tmp_path / ".ossature"
    config.metadata_path.mkdir()
    config.context_path = tmp_path / "context"
    config.context_path.mkdir()
    config.llm.model_for.return_value = "test-model"
    config.build.max_fix_attempts = 3
    return config


class FakeBackend:
    """Controllable backend for testing build_task orchestration."""

    def __init__(
        self,
        verify_results: list[tuple[bool, str]] | None = None,
        fix_side_effects: list[str | AgentRunError] | None = None,
    ) -> None:
        self._verify_results = list(verify_results or [(True, "")])
        self._verify_idx = 0
        self._fix_side_effects = list(fix_side_effects or [])
        self._fix_idx = 0
        self.generate_calls: list[str] = []
        self.fix_calls: list[str] = []
        self.verify_calls: list[str] = []

    def generate(
        self,
        prompt: str,
        ctx: BuildContext,
        console: Any,
        tracker: UsageTracker,
        model_name: str,
    ) -> str:
        self.generate_calls.append(prompt)
        return "generated code"

    def fix(
        self,
        prompt: str,
        ctx: BuildContext,
        console: Any,
        tracker: UsageTracker,
        model_name: str,
    ) -> str:
        self.fix_calls.append(prompt)
        idx = self._fix_idx
        self._fix_idx += 1
        if idx < len(self._fix_side_effects):
            effect = self._fix_side_effects[idx]
            if isinstance(effect, AgentRunError):
                raise effect
            # Simulate fixer creating a file so it's not detected as a no-op
            ctx.created_files.append(f"fix-file-{idx}.rs")
            return effect
        ctx.created_files.append(f"fix-file-{idx}.rs")
        return "fix applied"

    def verify(self, command: str, cwd: Path) -> tuple[bool, str]:
        self.verify_calls.append(command)
        idx = self._verify_idx
        self._verify_idx += 1
        if idx < len(self._verify_results):
            return self._verify_results[idx]
        return self._verify_results[-1]


def _run(
    tmp_path: Path,
    backend: FakeBackend,
    verify: str = "cargo test",
):
    task = _make_task(verify=verify)
    config = _make_config(tmp_path)
    console = MagicMock()
    status = MagicMock()
    return build_task(task, config, "build prompt", console, status, backend=backend)


class TestBuildTaskNoVerify:
    def test_no_verify_returns_success(self, tmp_path: Path) -> None:
        backend = FakeBackend()
        task = _make_task(verify="")
        config = _make_config(tmp_path)
        result = build_task(task, config, "build prompt", MagicMock(), MagicMock(), backend=backend)
        assert result.success is True
        assert len(backend.generate_calls) == 1
        assert len(backend.verify_calls) == 0
        assert len(backend.fix_calls) == 0


class TestBuildTaskVerifyPassesImmediately:
    def test_verify_passes_first_try(self, tmp_path: Path) -> None:
        backend = FakeBackend(verify_results=[(True, "all tests passed")])
        result = _run(tmp_path, backend)
        assert result.success is True
        assert len(backend.verify_calls) == 1
        assert len(backend.fix_calls) == 0


class TestBuildTaskVerifyCommandError:
    def test_invalid_verify_command_skips_fix_loop(self, tmp_path: Path) -> None:
        backend = FakeBackend(verify_results=[(False, "bash: nimc: command not found")])
        result = _run(tmp_path, backend)
        assert result.success is False
        assert len(backend.fix_calls) == 0


class TestBuildTaskFixLoop:
    def test_fix_then_verify_passes(self, tmp_path: Path) -> None:
        backend = FakeBackend(
            verify_results=[
                (False, "error: expected ';'"),  # initial verify
                (True, "all tests passed"),  # re-verify after fix
            ],
            fix_side_effects=["fixed the semicolon"],
        )
        result = _run(tmp_path, backend)
        assert result.success is True
        assert len(backend.fix_calls) == 1
        assert len(backend.verify_calls) == 2

    def test_all_fix_attempts_exhausted(self, tmp_path: Path) -> None:
        backend = FakeBackend(
            verify_results=[(False, "error: type mismatch")],
            fix_side_effects=["fix 1", "fix 2", "fix 3"],
        )
        result = _run(tmp_path, backend)
        assert result.success is False
        assert len(backend.fix_calls) == 3
        # 1 initial + 3 re-verifies
        assert len(backend.verify_calls) == 4

    def test_fix_agent_error_increments_attempt(self, tmp_path: Path) -> None:
        backend = FakeBackend(
            verify_results=[
                (False, "error: undefined variable"),
                (True, "ok"),
            ],
            fix_side_effects=[
                AgentRunError("LLM error"),
                "real fix",
            ],
        )
        result = _run(tmp_path, backend)
        assert result.success is True
        assert len(backend.fix_calls) == 2

    def test_noop_fix_retries_without_counting_attempt(self, tmp_path: Path) -> None:
        """Fixer that makes no file changes should retry without incrementing attempt."""

        class NoopThenFixBackend(FakeBackend):
            def fix(self, prompt, ctx, console, tracker, model_name):
                self.fix_calls.append(prompt)
                idx = len(self.fix_calls) - 1
                if idx == 0:
                    # No-op: don't add any files
                    return "I looked at it but didn't change anything"
                # Real fix on second call
                ctx.created_files.append("fixed.rs")
                return "applied fix"

        backend = NoopThenFixBackend(
            verify_results=[
                (False, "error"),
                (True, "ok"),
            ],
        )
        result = _run(tmp_path, backend)
        assert result.success is True
        # 2 fix calls: 1 no-op + 1 real
        assert len(backend.fix_calls) == 2


class TestBuildTaskArtifacts:
    def test_prompt_and_response_written(self, tmp_path: Path) -> None:
        backend = FakeBackend(verify_results=[(True, "ok")])
        task = _make_task()
        config = _make_config(tmp_path)
        build_task(task, config, "build prompt", MagicMock(), MagicMock(), backend=backend)

        task_dir = next(iter((config.metadata_path / "tasks").iterdir()))
        assert (task_dir / "prompt.md").read_text() == "build prompt"
        assert (task_dir / "response.md").read_text() == "generated code"
