import difflib
import os
import typing
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli
from pydantic_ai.models import KnownModelName, parse_model_id
from pydantic_ai.providers import infer_provider_class


class ConfigError(Exception):
    pass


@dataclass
class OutputConfig:
    dir: str = "output"
    language: str = "python"
    framework: str | None = None


@dataclass
class TestConfig:
    runner: str = "pytest"
    coverage: bool = True
    coverage_threshold: float = 80.0


@dataclass
class AuditConfig:
    max_fix_cycles: int = 3


@dataclass
class BuildConfig:
    max_fix_attempts: int = 3
    max_inline_lines: int = 200
    setup: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    test: list[str] = field(default_factory=list)


DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"

TOOL_REQUIRED_ROLES = frozenset({"build", "fixer"})


@dataclass
class LLMConfig:
    model: str = DEFAULT_MODEL
    audit: str | None = None
    build: str | None = None
    planner: str | None = None
    brief: str | None = None
    interface: str | None = None
    fixer: str | None = None
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    retries: int = 3
    tool_retries: int = 5

    @property
    def uses_ollama(self) -> bool:
        models = [
            self.model,
            self.audit,
            self.build,
            self.planner,
            self.brief,
            self.interface,
            self.fixer,
        ]
        return any(m is not None and m.startswith("ollama:") for m in models)

    def model_for(self, role: str) -> str:
        override: str | None = getattr(self, role, None)
        if override is not None:
            return override
        return self.model


@dataclass
class OssatureConfig:
    name: str = "ossature-project"
    version: str = "0.0.1"
    spec_dir: str = "specs"
    context_dir: str = "context"

    output: OutputConfig = field(default_factory=OutputConfig)
    test: TestConfig = field(default_factory=TestConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    build: BuildConfig = field(default_factory=BuildConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    root: Path = field(default_factory=Path.cwd)

    @property
    def spec_path(self) -> Path:
        return self.root / self.spec_dir

    @property
    def context_path(self) -> Path:
        return self.root / self.context_dir

    @property
    def output_path(self) -> Path:
        return self.root / self.output.dir

    @property
    def metadata_path(self) -> Path:
        return self.root / ".ossature"

    @property
    def metadata_context_path(self) -> Path:
        return self.metadata_path / "context"

    @property
    def metadata_context_spec_briefs_path(self) -> Path:
        return self.metadata_context_path / "spec-briefs"

    @property
    def metadata_context_interfaces_path(self) -> Path:
        return self.metadata_context_path / "interfaces"

    @property
    def metadata_snapshots_path(self) -> Path:
        return self.metadata_path / "snapshots"

    @property
    def metadata_planners_path(self) -> Path:
        return self.metadata_path / "planners"

    @property
    def is_audited(self) -> bool:
        return self.metadata_path.exists()


def find_config(start_path: Path | None = None) -> Path | None:
    current = start_path or Path.cwd()
    current = current.resolve()

    while current != current.parent:
        config_path = current / "ossature.toml"
        if config_path.exists():
            return config_path
        current = current.parent

    config_path = current / "ossature.toml"
    if config_path.exists():
        return config_path

    return None


def _parse_output_config(data: dict[str, Any]) -> OutputConfig:
    return OutputConfig(
        dir=data.get("dir", "."),
        language=data.get("language", "python"),
    )


def _parse_test_config(data: dict[str, Any]) -> TestConfig:
    return TestConfig(
        runner=data.get("runner", "pytest"),
        coverage=data.get("coverage", True),
        coverage_threshold=float(data.get("coverage_threshold", 80.0)),
    )


def _parse_audit_config(data: dict[str, Any]) -> AuditConfig:
    return AuditConfig(
        max_fix_cycles=int(data.get("max_fix_cycles", 3)),
    )


def _coerce_command_list(value: Any) -> list[str]:
    """Normalize a build-command field to a list of command strings.

    Accepts a single shell-command string (legacy form) or a list of
    strings. An empty/missing value becomes an empty list so absence is
    represented uniformly.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    raise ConfigError(
        f"build command must be a string or a list of strings, got {type(value).__name__}"
    )


def _parse_build_config(data: dict[str, Any]) -> BuildConfig:
    return BuildConfig(
        max_fix_attempts=int(data.get("max_fix_attempts", 3)),
        max_inline_lines=int(data.get("max_inline_lines", 200)),
        setup=_coerce_command_list(data.get("setup")),
        verify=_coerce_command_list(data.get("verify")),
        test=_coerce_command_list(data.get("test")),
    )


def _parse_llm_config(data: dict[str, Any]) -> LLMConfig:
    return LLMConfig(
        model=data.get("model", DEFAULT_MODEL),
        audit=data.get("audit"),
        build=data.get("build"),
        planner=data.get("planner"),
        brief=data.get("brief"),
        interface=data.get("interface"),
        fixer=data.get("fixer"),
        ollama_base_url=data.get("ollama_base_url", DEFAULT_OLLAMA_BASE_URL),
        retries=int(data.get("retries", 3)),
        tool_retries=int(data.get("tool_retries", 5)),
    )


def load_config(path: Path | None = None) -> OssatureConfig:
    if path is None:
        path = find_config()
        if path is None:
            raise ConfigError("No ossature.toml found.")

    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        content = path.read_text(encoding="utf-8")
        data = tomli.loads(content)
    except Exception as e:
        raise ConfigError(f"Failed to parse {path}: {e}") from e

    project = data.get("project", {})

    llm_data = data.get("llm")
    if llm_data is None:
        raise ConfigError(
            "Missing [llm] section in ossature.toml. Add at minimum:\n\n"
            "  [llm]\n"
            '  model = "anthropic:claude-sonnet-4-6"'
        )
    if "model" not in llm_data:
        raise ConfigError(
            "Missing 'model' in [llm] section. Set the default model, e.g.:\n\n"
            "  [llm]\n"
            '  model = "anthropic:claude-sonnet-4-6"'
        )

    config = OssatureConfig(
        name=project.get("name", "ossature-project"),
        version=project.get("version", "0.0.1"),
        spec_dir=project.get("spec_dir", "specs"),
        context_dir=project.get("context_dir", "context"),
        output=_parse_output_config(data.get("output", {})),
        test=_parse_test_config(data.get("test", {})),
        audit=_parse_audit_config(data.get("audit", {})),
        build=_parse_build_config(data.get("build", {})),
        llm=_parse_llm_config(llm_data),
        root=path.parent,
    )

    if config.llm.uses_ollama and "OLLAMA_BASE_URL" not in os.environ:
        os.environ["OLLAMA_BASE_URL"] = config.llm.ollama_base_url

    _warn_unknown_models(config.llm)
    _warn_redundant_cd(config)

    return config


_LLM_ROLE_FIELDS: tuple[str, ...] = (
    "model",
    "audit",
    "build",
    "planner",
    "brief",
    "interface",
    "fixer",
)


def _known_model_names() -> tuple[str, ...]:
    """Return pydantic_ai's curated `KnownModelName` literal as a tuple.

    `KnownModelName` is a `TypeAliasType`; the underlying `Literal` lives at
    `.__value__`. If pydantic_ai restructures the type, return an empty
    tuple so the caller skips the model-name check rather than crashing.
    """
    try:
        return typing.get_args(KnownModelName.__value__)
    except AttributeError:
        return ()


def _warn_unknown_models(llm: LLMConfig) -> None:
    """Warn when a provider or model name in [llm] looks misspelled.

    Missing provider prefixes are detected with parse_model_id, bad
    providers with infer_provider_class. KnownModelName is used as a list
    of typo suggestions. This only warns, so loading still completes and
    any real failure later surfaces the original pydantic_ai error.
    """
    known = _known_model_names()
    known_set = set(known)
    known_providers = sorted({k.split(":", 1)[0] for k in known if ":" in k})

    for field_name in _LLM_ROLE_FIELDS:
        value = getattr(llm, field_name, None)
        if not value:
            continue
        _check_model_string(field_name, value, known_set, known_providers)


def _check_model_string(
    field_name: str,
    value: str,
    known_set: set[str],
    known_providers: list[str],
) -> None:
    if value == "test":
        # "test" is a sentinel that pydantic_ai's infer_model accepts
        # directly and turns into a TestModel, so don't try to parse it.
        return

    provider, _ = parse_model_id(value)
    if provider is None:
        # No "provider:" prefix and not a legacy bare name that pydantic_ai
        # recognizes, so infer_model would raise UserError("Unknown model: ...").
        matches = difflib.get_close_matches(value, list(known_set), n=3, cutoff=0.5)
        suggestion = f" Did you mean: {', '.join(matches)}?" if matches else ""
        warnings.warn(
            f"[llm] {field_name} = {value!r}: Unknown model: {value}. "
            f"Names must use the form 'provider:model' (e.g. 'openai:gpt-5').{suggestion}",
            stacklevel=4,
        )
        return

    try:
        infer_provider_class(provider)
    except ValueError as e:
        matches = difflib.get_close_matches(provider, known_providers, n=3, cutoff=0.5)
        suggestion = f" Did you mean: {', '.join(matches)}?" if matches else ""
        warnings.warn(
            f"[llm] {field_name} = {value!r}: {e}.{suggestion}",
            stacklevel=4,
        )
        return

    # Provider was accepted; cross-check the model name against the curated list.
    if not known_set or value in known_set:
        return
    if provider not in {k.split(":", 1)[0] for k in known_set}:
        # Provider is real but not listed in KnownModelName, like ollama,
        # openrouter, or litellm, where model names are free-form.
        return
    siblings = [k for k in known_set if k.startswith(f"{provider}:")]
    matches = difflib.get_close_matches(value, siblings, n=3, cutoff=0.5)
    suggestion = f" Did you mean: {', '.join(matches)}?" if matches else ""
    warnings.warn(
        f"[llm] {field_name} = {value!r}: model not recognized by pydantic_ai. "
        f"It may still work if recently released, otherwise check for typos.{suggestion}",
        stacklevel=4,
    )


def _warn_redundant_cd(config: OssatureConfig) -> None:
    output_dir = config.output.dir
    prefix = f"cd {output_dir}"
    fields = {"setup": config.build.setup, "verify": config.build.verify, "test": config.build.test}
    for field_name, commands in fields.items():
        for command in commands:
            stripped = command.lstrip()
            if not stripped.startswith(prefix):
                continue
            rest = stripped[len(prefix) :]
            if rest == "" or rest[0] in (" ", "\t", ";", "&"):
                warnings.warn(
                    f"[build] {field_name} contains 'cd {output_dir}' — "
                    f"this is unnecessary. All build commands already run "
                    f"inside the output directory ({output_dir!r}).",
                    stacklevel=2,
                )
                break
