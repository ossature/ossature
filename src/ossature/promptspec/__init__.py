# Importing the specs subpackage triggers registration of every shipped
# PromptSpec as a side effect.
from ossature.promptspec import specs as _specs  # noqa: F401
from ossature.promptspec.renderer import register, registered_ids, render
from ossature.promptspec.spec import Block, PromptSpec

__all__ = ["Block", "PromptSpec", "register", "registered_ids", "render"]
