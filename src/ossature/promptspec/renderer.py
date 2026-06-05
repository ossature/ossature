from dataclasses import fields as dataclass_fields
from string import Template

from ossature.promptspec.profile import LanguageProfile, resolve_profile
from ossature.promptspec.spec import PromptSpec

_REGISTRY: dict[str, PromptSpec] = {}


class PromptSpecError(Exception):
    pass


def register(spec: PromptSpec) -> None:
    if spec.id in _REGISTRY:
        raise PromptSpecError(f"duplicate PromptSpec id: {spec.id!r}")
    _REGISTRY[spec.id] = spec


def registered_ids() -> list[str]:
    return sorted(_REGISTRY)


def _profile_substitutions(language: str) -> dict[str, str]:
    """Expand a LanguageProfile into a Template substitution namespace.

    Each profile field is itself run through Template.safe_substitute
    with `language=` available, so a generic profile can leave
    `${language}` in its field values and have it filled in at render
    time. `safe_substitute` keeps any unrelated `$` characters intact.
    """
    profile = resolve_profile(language)
    namespace: dict[str, str] = {}
    for field in dataclass_fields(LanguageProfile):
        value = getattr(profile, field.name)
        # Only string fields participate in prompt substitution. Tuple fields
        # like build_invocation_tokens and source_extensions are consumed by
        # the verify validator, not by the renderer.
        if not isinstance(value, str):
            continue
        namespace[field.name] = Template(value).safe_substitute(language=language)
    return namespace


def render(spec_id: str, **variables: str) -> str:
    """Render a registered PromptSpec to its final string.

    When `language` is one of the spec's declared variables, the active
    language profile's fields are also pulled into the substitution
    namespace, so prompt templates can use `${build_invocation_examples}`
    and the like alongside `${language}`.
    """
    try:
        spec = _REGISTRY[spec_id]
    except KeyError:
        raise PromptSpecError(f"unknown PromptSpec id: {spec_id!r}") from None

    provided = set(variables)
    missing = spec.variables - provided
    if missing:
        raise PromptSpecError(f"{spec_id}: missing variables {sorted(missing)}")
    unknown = provided - spec.variables
    if unknown:
        raise PromptSpecError(f"{spec_id}: unknown variables {sorted(unknown)}")

    raw = "\n\n".join(b.content for b in spec.blocks)
    if not spec.variables:
        return raw

    namespace = dict(variables)
    if "language" in spec.variables:
        namespace.update(_profile_substitutions(variables["language"]))
        # Caller-provided values take precedence over profile defaults.
        namespace.update(variables)
    return Template(raw).substitute(**namespace)
