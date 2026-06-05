"""Unit tests for the post-processing verify-command validator.

The validator's core is language-agnostic. Most of the test cases use
the rust profile because it has clearly typed source extensions and
build tokens, but the negative-case tests use the generic profile to
prove unknown languages get no false positives.
"""

from dataclasses import replace

import pytest

from ossature.audit.verify_validator import (
    VerifyValidationError,
    check_verify_commands,
    format_validator_errors,
)
from ossature.models.plan import PlannerTask, PreservedTaskRef, SpecTaskPlan
from ossature.promptspec import resolve_profile


def _task(
    title: str,
    outputs: list[str],
    verify: list[str],
    *,
    depends_on: list[int] | None = None,
    source: list[str] | None = None,
) -> PlannerTask:
    return PlannerTask(
        title=title,
        description="",
        outputs=outputs,
        depends_on=depends_on or [],
        spec_refs=[],
        arch_refs=[],
        verify=verify,
        source=source or [],
    )


class TestBuildBeforeSource:
    def test_scaffold_with_build_command_is_flagged(self):
        rust = resolve_profile("rust")
        plan = SpecTaskPlan(tasks=[_task("Scaffold Cargo.toml", ["Cargo.toml"], ["cargo build"])])
        errors = check_verify_commands(plan, rust)
        assert len(errors) == 1
        assert errors[0].task_index == 1
        assert errors[0].verify_command == "cargo build"
        assert "no rust source files" in errors[0].reason

    def test_source_in_predecessor_makes_build_safe(self):
        rust = resolve_profile("rust")
        plan = SpecTaskPlan(
            tasks=[
                _task("Lib", ["src/lib.rs"], ["cargo check"]),
                _task(
                    "Scaffold then test",
                    ["Cargo.toml"],
                    ["cargo build"],
                    depends_on=[1],
                ),
            ]
        )
        errors = check_verify_commands(plan, rust)
        assert errors == []

    def test_source_in_same_task_makes_build_safe(self):
        rust = resolve_profile("rust")
        plan = SpecTaskPlan(tasks=[_task("Lib + check", ["src/lib.rs"], ["cargo build"])])
        errors = check_verify_commands(plan, rust)
        assert errors == []

    def test_predecessor_without_dependency_does_not_count(self):
        # Task 2 doesn't list task 1 as a dependency, so task 1's source
        # output doesn't count as visible to task 2's verify.
        rust = resolve_profile("rust")
        plan = SpecTaskPlan(
            tasks=[
                _task("Lib", ["src/lib.rs"], ["cargo check"]),
                _task("Scaffold", ["Cargo.toml"], ["cargo build"], depends_on=[]),
            ]
        )
        errors = check_verify_commands(plan, rust)
        assert len(errors) == 1
        assert errors[0].task_index == 2


class TestSafeCommands:
    def test_cargo_check_is_not_a_build_invocation(self):
        rust = resolve_profile("rust")
        # cargo check is in safe_verify_examples, not build_invocation_tokens
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["Cargo.toml"], ["cargo check"])])
        errors = check_verify_commands(plan, rust)
        assert errors == []

    def test_typescript_noemit_is_safe(self):
        ts = resolve_profile("typescript")
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["tsconfig.json"], ["npx tsc --noEmit"])])
        errors = check_verify_commands(plan, ts)
        assert errors == []

    def test_typescript_tsc_build_is_flagged(self):
        ts = resolve_profile("typescript")
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["tsconfig.json"], ["tsc --build"])])
        errors = check_verify_commands(plan, ts)
        assert len(errors) == 1

    def test_zig_ast_check_is_safe(self):
        zig = resolve_profile("zig")
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["build.zig"], ["zig ast-check build.zig"])])
        errors = check_verify_commands(plan, zig)
        assert errors == []

    def test_zig_build_variants_are_flagged(self):
        zig = resolve_profile("zig")
        plan = SpecTaskPlan(
            tasks=[
                _task("S1", ["build.zig"], ["zig build"]),
                _task("S2", ["build.zig"], ["zig build run"]),
                _task("S3", ["build.zig"], ["zig build test"]),
            ]
        )
        errors = check_verify_commands(plan, zig)
        assert len(errors) == 3


class TestSkippedTasks:
    def test_copy_task_is_skipped(self):
        rust = resolve_profile("rust")
        plan = SpecTaskPlan(
            tasks=[
                _task(
                    "Copy assets",
                    ["src/assets/x.mp3"],
                    [],
                    source=["assets/x.mp3"],
                )
            ]
        )
        errors = check_verify_commands(plan, rust)
        assert errors == []

    def test_preserved_ref_predecessor_skips_check_conservatively(self):
        # If a task depends on a preserved ref we don't know that ref's
        # outputs, so the validator declines to flag rather than emit a
        # false positive.
        rust = resolve_profile("rust")
        plan = SpecTaskPlan(
            tasks=[
                PreservedTaskRef(previous_index=1, depends_on=[]),
                _task("Build", ["Cargo.toml"], ["cargo build"], depends_on=[1]),
            ]
        )
        errors = check_verify_commands(plan, rust)
        assert errors == []


class TestProfileGating:
    def test_generic_profile_never_flags(self):
        generic = resolve_profile("elixir")
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["mix.exs"], ["mix compile"])])
        errors = check_verify_commands(plan, generic)
        assert errors == []

    def test_empty_tokens_disable_check(self):
        rust = resolve_profile("rust")
        # Sanity: rust normally flags this
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["Cargo.toml"], ["cargo build"])])
        assert len(check_verify_commands(plan, rust)) == 1

        # If we hand-roll a profile with empty tokens, no errors
        empty = replace(rust, build_invocation_tokens=())
        assert check_verify_commands(plan, empty) == []

    def test_empty_extensions_disable_check(self):
        rust = resolve_profile("rust")
        empty = replace(rust, source_extensions=())
        plan = SpecTaskPlan(tasks=[_task("Scaffold", ["Cargo.toml"], ["cargo build"])])
        assert check_verify_commands(plan, empty) == []


class TestFormatting:
    def test_format_validator_errors_includes_task_and_command(self):
        err = VerifyValidationError(
            task_index=2,
            task_title="Build",
            verify_command="cargo build",
            reason="runs a build command but no rust source files (.rs) exist yet.",
        )
        out = format_validator_errors([err])
        assert "Task 2" in out
        assert "'Build'" in out
        assert "cargo build" in out
        assert "no rust source files" in out

    def test_format_validator_errors_handles_multiple(self):
        errs = [
            VerifyValidationError(
                task_index=i,
                task_title=f"T{i}",
                verify_command="cargo build",
                reason="missing source",
            )
            for i in (1, 2)
        ]
        out = format_validator_errors(errs)
        assert "Task 1" in out
        assert "Task 2" in out


class TestRealisticPlanShapes:
    def test_valid_python_plan_passes(self):
        py = resolve_profile("python")
        plan = SpecTaskPlan(
            tasks=[
                _task(
                    "Scaffold pyproject",
                    ["pyproject.toml"],
                    ["test -f pyproject.toml"],
                ),
                _task(
                    "Types",
                    ["src/auth/types.py"],
                    ["python -m py_compile src/auth/types.py"],
                    depends_on=[1],
                ),
                _task(
                    "Tests",
                    ["tests/test_auth.py"],
                    ["python -m pytest tests/test_auth.py -q"],
                    depends_on=[1, 2],
                ),
            ]
        )
        assert check_verify_commands(plan, py) == []

    def test_python_scaffold_running_pip_install_is_flagged(self):
        py = resolve_profile("python")
        plan = SpecTaskPlan(
            tasks=[
                _task(
                    "Scaffold pyproject",
                    ["pyproject.toml"],
                    ["pip install -e ."],
                )
            ]
        )
        errors = check_verify_commands(plan, py)
        assert len(errors) == 1
        assert "no python source files" in errors[0].reason


@pytest.mark.parametrize(
    ("lang", "scaffold", "bad_verify"),
    [
        ("python", "pyproject.toml", "pip install -e ."),
        ("rust", "Cargo.toml", "cargo build"),
        ("javascript", "package.json", "npm install"),
        ("typescript", "tsconfig.json", "tsc --build"),
        ("lua", "conf.lua", "luarocks make"),
        ("zig", "build.zig", "zig build"),
    ],
)
def test_each_curated_profile_catches_scaffold_build(lang, scaffold, bad_verify):
    profile = resolve_profile(lang)
    plan = SpecTaskPlan(tasks=[_task(f"Scaffold {scaffold}", [scaffold], [bad_verify])])
    errors = check_verify_commands(plan, profile)
    assert len(errors) == 1, f"{lang} did not catch {bad_verify!r} on a {scaffold} scaffold task"
