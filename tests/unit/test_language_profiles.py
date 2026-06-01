"""Behavior tests for the LanguageProfile mechanism.

Covers the resolver, the renderer's profile injection, and the
cross-language leakage guarantee: a prompt rendered for one curated
language must not mention another curated language's exclusive tools.
TypeScript and JavaScript share npm and node, so their leakage check
only forbids the other side's exclusive tooling (tsc/tsconfig on the JS
side, node --check/--test on the TS side).
"""

import pytest

from ossature.promptspec import profile as profile_module
from ossature.promptspec import (
    registered_profile_names,
    render,
    resolve_profile,
)
from ossature.promptspec.profile import (
    LanguageProfile,
    ProfileError,
    register_generic,
    register_profile,
)


def _make_dummy_profile(name: str) -> LanguageProfile:
    return LanguageProfile(
        name=name,
        setup_command_example="x",
        setup_manifest_example="x",
        scaffold_manifests="x",
        build_invocation_examples="x",
        safe_verify_examples="x",
        common_verify_command="x",
        worked_examples="x",
    )


# Tool name fragments that uniquely identify each curated language.
# JS and TS are split into "shared" and "exclusive" because they sit on
# the same npm/node toolchain.
_PYTHON_FRAGMENTS = ("pyproject", "pip install", "python -m py_compile", "pytest")
_RUST_FRAGMENTS = ("cargo", "Cargo.toml", "rustc", "target/release")
_JS_EXCLUSIVE = ("node --check", "node --test")
_TS_EXCLUSIVE = ("tsc", "tsconfig", "npx tsc")
_LUA_FRAGMENTS = ("luac", "love .", "rockspec", "conf.lua")
_ZIG_FRAGMENTS = ("zig ast-check", "zig build", "build.zig", "zig-out")


_CURATED_LANGUAGES = ("python", "rust", "javascript", "typescript", "lua", "zig")


class TestResolver:
    @pytest.mark.parametrize("lang", _CURATED_LANGUAGES)
    def test_curated_match_wins(self, lang: str) -> None:
        assert resolve_profile(lang).name == lang

    def test_unknown_language_falls_back_to_generic(self) -> None:
        assert resolve_profile("elixir").name == "__generic__"
        assert resolve_profile("kotlin").name == "__generic__"


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

    def test_lua_profile_fields_present(self) -> None:
        out = render("audit.plan_generation", language="lua")
        assert "luac -p" in out
        assert "conf.lua" in out

    def test_zig_profile_fields_present(self) -> None:
        out = render("audit.plan_generation", language="zig")
        assert "zig ast-check" in out
        assert "build.zig" in out
        assert "zig build" in out

    def test_generic_profile_interpolates_language_name(self) -> None:
        out = render("audit.plan_generation", language="elixir")
        assert "elixir" in out


# Maps each curated language to the fragment groups that must NOT
# appear in its render. JS/TS each omit the other's shared npm/node
# tooling but still exclude each other's exclusive bits.
_LEAKAGE_FORBIDDEN: dict[str, tuple[tuple[str, ...], ...]] = {
    "python": (_RUST_FRAGMENTS, _JS_EXCLUSIVE, _TS_EXCLUSIVE, _LUA_FRAGMENTS, _ZIG_FRAGMENTS),
    "rust": (_PYTHON_FRAGMENTS, _JS_EXCLUSIVE, _TS_EXCLUSIVE, _LUA_FRAGMENTS, _ZIG_FRAGMENTS),
    "javascript": (
        _PYTHON_FRAGMENTS,
        _RUST_FRAGMENTS,
        _TS_EXCLUSIVE,
        _LUA_FRAGMENTS,
        _ZIG_FRAGMENTS,
    ),
    "typescript": (
        _PYTHON_FRAGMENTS,
        _RUST_FRAGMENTS,
        _JS_EXCLUSIVE,
        _LUA_FRAGMENTS,
        _ZIG_FRAGMENTS,
    ),
    "lua": (_PYTHON_FRAGMENTS, _RUST_FRAGMENTS, _JS_EXCLUSIVE, _TS_EXCLUSIVE, _ZIG_FRAGMENTS),
    "zig": (_PYTHON_FRAGMENTS, _RUST_FRAGMENTS, _JS_EXCLUSIVE, _TS_EXCLUSIVE, _LUA_FRAGMENTS),
}


class TestCrossLanguageLeakage:
    @pytest.mark.parametrize("lang", _CURATED_LANGUAGES)
    def test_curated_render_excludes_other_languages(self, lang: str) -> None:
        out = render("audit.plan_generation", language=lang)
        for group in _LEAKAGE_FORBIDDEN[lang]:
            for frag in group:
                assert frag not in out, f"{lang} render leaked {frag!r}"

    def test_generic_render_excludes_all_curated_tools(self) -> None:
        out = render("audit.plan_generation", language="elixir")
        for group in (
            _PYTHON_FRAGMENTS,
            _RUST_FRAGMENTS,
            _JS_EXCLUSIVE,
            _TS_EXCLUSIVE,
            _LUA_FRAGMENTS,
            _ZIG_FRAGMENTS,
        ):
            for frag in group:
                assert frag not in out, f"generic render leaked {frag!r}"


class TestProfileRegistry:
    def test_duplicate_profile_rejected(self) -> None:
        with pytest.raises(ProfileError, match="duplicate language profile"):
            register_profile(_make_dummy_profile("python"))

    def test_duplicate_generic_rejected(self) -> None:
        with pytest.raises(ProfileError, match="generic profile already registered"):
            register_generic(_make_dummy_profile("__second_generic__"))

    def test_resolve_without_generic_fallback_raises(self) -> None:
        # The generic fallback is registered at import time, so temporarily
        # pop it to exercise the no-fallback branch. The try/finally restores
        # registry state even if the assertion fails.
        saved = profile_module._REGISTRY.pop(profile_module._GENERIC_KEY)
        try:
            with pytest.raises(ProfileError, match="no curated profile for 'made_up_lang'"):
                resolve_profile("made_up_lang")
        finally:
            profile_module._REGISTRY[profile_module._GENERIC_KEY] = saved

    def test_registered_profile_names_excludes_generic(self) -> None:
        names = registered_profile_names()
        assert "__generic__" not in names
        for curated in ("python", "rust", "javascript", "typescript", "lua", "zig"):
            assert curated in names
