from string import Template

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


def render(spec_id: str, **variables: str) -> str:
    """Render a registered PromptSpec to its final string."""
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
    return Template(raw).substitute(**variables)
