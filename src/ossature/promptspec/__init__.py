# Importing the profiles and specs subpackages triggers registration of
# every shipped LanguageProfile and PromptSpec as a side effect.
from ossature.promptspec import profiles as _profiles  # noqa: F401
from ossature.promptspec import specs as _specs  # noqa: F401
from ossature.promptspec.profile import (
    LanguageProfile,
    registered_profile_names,
    resolve_profile,
)
from ossature.promptspec.renderer import register, registered_ids, render
from ossature.promptspec.spec import Block, PromptSpec

__all__ = [
    "Block",
    "LanguageProfile",
    "PromptSpec",
    "register",
    "registered_ids",
    "registered_profile_names",
    "render",
    "resolve_profile",
]
