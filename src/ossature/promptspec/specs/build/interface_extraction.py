from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

_ROLE = """\
<role>
You are extracting the public interface from generated ${language} source code.
</role>"""

_INSTRUCTIONS = """\
<instructions>
Given the source files for a module, produce a markdown document containing:
- All public types, structs/classes, enums with their fields
- All public function/method signatures with types
- Error types

Write interfaces in idiomatic ${language} using fenced code blocks.
Organize by file with clear headers.

Do NOT include:
- Implementation bodies (use `...` or `pass`)
- Private/internal types
- Tests or build configuration
- Comments unless they are doc comments that define behavior

This document serves as the contract for dependent modules — downstream code generation will rely on these signatures to integrate correctly.
</instructions>"""

_FOOTER = "Output only the interface document."

SPEC = PromptSpec(
    id="build.interface_extraction",
    version="1.0.0",
    variables=frozenset({"language"}),
    blocks=(
        Block("role", _ROLE),
        Block("instructions", _INSTRUCTIONS),
        Block("footer", _FOOTER),
    ),
)

register(SPEC)
