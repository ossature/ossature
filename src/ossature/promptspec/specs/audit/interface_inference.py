from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

_ROLE = """\
<role>
You are a senior ${language} architect. Given a software specification (SMD), design the public interface surface that this module will expose.
</role>"""

_INSTRUCTIONS = """\
<instructions>
Output a markdown document containing:
- Module/file structure with paths
- All public types, structs/classes, enums with their fields
- All public function/method signatures with types
- Error types

Write interfaces in idiomatic ${language} using fenced code blocks.
Organize by component with clear headers.

Do NOT include:
- Implementation bodies (use `...` or `pass`)
- Private/internal types
- Tests or build configuration

This document serves as the contract for dependent modules — downstream code generation will rely on these signatures to integrate correctly.
</instructions>"""

_FOOTER = "Output only the interface document."

SPEC = PromptSpec(
    id="audit.interface_inference",
    version="1.0.0",
    variables=frozenset({"language"}),
    blocks=(
        Block("role", _ROLE),
        Block("instructions", _INSTRUCTIONS),
        Block("footer", _FOOTER),
    ),
)

register(SPEC)
