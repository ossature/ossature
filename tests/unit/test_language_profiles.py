"""Behavior tests for the LanguageProfile mechanism.

Covers the resolver, the renderer's profile injection, and the
cross-language leakage guarantee: a prompt rendered for one curated
language must not mention another curated language's exclusive tools.
TypeScript and JavaScript share npm and node, so their leakage check
only forbids the other side's exclusive tooling (tsc/tsconfig on the JS
side, node --check/--test on the TS side).
"""

import pytest

from ossature.promptspec import render, resolve_profile
from ossature.promptspec.profile import (
    LanguageProfile,
    ProfileError,
    register_profile,
)

# Tool name fragments that uniquely identify each curated language.
# JS and TS are split into "shared" and "exclusive" because they sit on
# the same npm/node toolchain.
_PYTHON_FRAGMENTS = ("pyproject", "pip install", "python -m py_compile", "pytest")
_RUST_FRAGMENTS = ("cargo", "Cargo.toml", "rustc")
_JS_EXCLUSIVE = ("node --check", "node --test")
_TS_EXCLUSIVE = ("tsc", "tsconfig", "npx tsc")


class TestResolver:
    def test_curated_match_wins(self) -> None:
        assert resolve_profile("python").name == "python"
        assert resolve_profile("rust").name == "rust"
        assert resolve_profile("javascript").name == "javascript"
        assert resolve_profile("typescript").name == "typescript"

    def test_unknown_language_falls_back_to_generic(self) -> None:
        assert resolve_profile("elixir").name == "__generic__"
        assert resolve_profile("zig").name == "__generic__"


class TestRendererInjection:
    def test_python_profile_fields_present(self) -> None:
        out = render("audit.plan_generation", language="python")
        assert "python -m py_compile" in out
        assert "pyproject.toml" in out
        assert "pytest" in out

    def test_rust_profile_fields_present(self) -> None:
        out = render("audit.plan_generation", language="rust")
        assert "cargo check" in out
        assert "Cargo.toml" in out
        assert "cargo test" in out

    def test_javascript_profile_fields_present(self) -> None:
        out = render("audit.plan_generation", language="javascript")
        assert "node --check" in out
        assert "node --test" in out
        assert "package.json" in out

    def test_typescript_profile_fields_present(self) -> None:
        out = render("audit.plan_generation", language="typescript")
        assert "tsc --noEmit" in out
        assert "tsconfig.json" in out

    def test_generic_profile_interpolates_language_name(self) -> None:
        out = render("audit.plan_generation", language="elixir")
        assert "elixir" in out


class TestCrossLanguageLeakage:
    def test_python_render_excludes_others(self) -> None:
        out = render("audit.plan_generation", language="python")
        for frag in (*_RUST_FRAGMENTS, *_JS_EXCLUSIVE, *_TS_EXCLUSIVE):
            assert frag not in out, f"python render leaked {frag!r}"

    def test_rust_render_excludes_others(self) -> None:
        out = render("audit.plan_generation", language="rust")
        for frag in (*_PYTHON_FRAGMENTS, *_JS_EXCLUSIVE, *_TS_EXCLUSIVE):
            assert frag not in out, f"rust render leaked {frag!r}"

    def test_javascript_render_excludes_python_rust_and_ts_exclusives(self) -> None:
        out = render("audit.plan_generation", language="javascript")
        for frag in (*_PYTHON_FRAGMENTS, *_RUST_FRAGMENTS, *_TS_EXCLUSIVE):
            assert frag not in out, f"javascript render leaked {frag!r}"

    def test_typescript_render_excludes_python_rust_and_js_exclusives(self) -> None:
        out = render("audit.plan_generation", language="typescript")
        for frag in (*_PYTHON_FRAGMENTS, *_RUST_FRAGMENTS, *_JS_EXCLUSIVE):
            assert frag not in out, f"typescript render leaked {frag!r}"

    def test_generic_render_excludes_all_curated_tools(self) -> None:
        out = render("audit.plan_generation", language="elixir")
        for frag in (
            *_PYTHON_FRAGMENTS,
            *_RUST_FRAGMENTS,
            *_JS_EXCLUSIVE,
            *_TS_EXCLUSIVE,
        ):
            assert frag not in out, f"generic render leaked {frag!r}"


class TestProfileRegistry:
    def test_duplicate_profile_rejected(self) -> None:
        dup = LanguageProfile(
            name="python",
            setup_command_example="x",
            setup_manifest_example="x",
            scaffold_manifests="x",
            build_invocation_examples="x",
            safe_verify_examples="x",
            common_verify_command="x",
            worked_examples="x",
        )
        with pytest.raises(ProfileError, match="duplicate language profile"):
            register_profile(dup)
