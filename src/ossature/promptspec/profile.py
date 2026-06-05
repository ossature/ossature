from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """Per-language data injected into prompts at render time.

    Profile fields are plain strings that may contain a `${language}`
    placeholder. The renderer substitutes that placeholder before
    injecting the field value into the main prompt template, so a
    generic profile can stay language-agnostic by writing
    "use the standard build command for ${language}" and letting the
    name be filled in at render time.

    Profiles exist for python, rust, javascript, typescript, lua, and
    zig. Anything else falls back to the generic profile.
    """

    name: str
    # Short prose example of a setup-time command that bootstraps a
    # project, like "cargo init" or "python -m venv .venv".
    setup_command_example: str
    # The file the setup command typically creates, like "Cargo.toml"
    # or "package.json". Used in the "don't redo what setup did" rule.
    setup_manifest_example: str
    # Comma-separated manifest filenames the scaffold-only rule covers
    # for this language, like "Cargo.toml, build.rs". Already formatted
    # for inline use in prose.
    scaffold_manifests: str
    # Build-invocation commands the scaffold-only rule forbids when the
    # source they depend on doesn't exist yet, like
    # "`cargo build`, `cargo run`". Already formatted with backticks.
    build_invocation_examples: str
    # A pre-formatted paragraph of lightweight verify examples specific
    # to this language. Used inside the planner's verify-rules block.
    safe_verify_examples: str
    # The verify command the planner should reach for first, like
    # "cargo check" or "python -m py_compile <files>".
    common_verify_command: str
    # A pre-formatted block of two or three worked task examples in this
    # language, used as the `<examples>` block of the planner prompt.
    worked_examples: str
    # Substrings the verify-command validator looks for to decide whether
    # a verify line invokes the build, like ("cargo build", "cargo run").
    # An empty tuple disables this check, which is what the generic
    # profile uses so unknown languages get no false positives.
    build_invocation_tokens: tuple[str, ...] = ()
    # File extensions that count as source files. Used by the verify
    # validator to tell whether a task or its predecessors have produced
    # anything compilable yet. An empty tuple disables that check.
    source_extensions: tuple[str, ...] = ()
    # Basenames of manifest files that share an extension with source
    # (e.g. zig's `build.zig`, lua's `conf.lua`). The validator excludes
    # these from source detection so a scaffold that only emits a
    # manifest still gets flagged when its verify runs a build.
    manifest_filenames: tuple[str, ...] = ()


_REGISTRY: dict[str, LanguageProfile] = {}
_GENERIC_KEY = "__generic__"


class ProfileError(Exception):
    pass


def register_profile(profile: LanguageProfile) -> None:
    if profile.name in _REGISTRY:
        raise ProfileError(f"duplicate language profile: {profile.name!r}")
    _REGISTRY[profile.name] = profile


def register_generic(profile: LanguageProfile) -> None:
    if _GENERIC_KEY in _REGISTRY:
        raise ProfileError("generic profile already registered")
    _REGISTRY[_GENERIC_KEY] = profile


def resolve_profile(language: str) -> LanguageProfile:
    """Return the curated profile for the language, or the generic fallback."""
    profile = _REGISTRY.get(language)
    if profile is not None:
        return profile
    generic = _REGISTRY.get(_GENERIC_KEY)
    if generic is None:
        raise ProfileError(
            f"no curated profile for {language!r} and no generic fallback registered"
        )
    return generic


def registered_profile_names() -> list[str]:
    """Return the list of curated profile names. Excludes the generic fallback."""
    return sorted(k for k in _REGISTRY if k != _GENERIC_KEY)
