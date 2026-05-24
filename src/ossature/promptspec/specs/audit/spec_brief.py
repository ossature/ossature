from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

_ROLE = """\
<role>
You are a technical writer creating a module summary for an LLM code generation system.
</role>"""

_INSTRUCTIONS = """\
<instructions>
Given a module's title, dependencies, and overview, write 2-3 sentences that capture:
- What this module does
- Its key responsibilities
- What it integrates with

Be concrete and technical. This summary provides context during code generation for related modules.
</instructions>"""

_FOOTER = "Output only the brief, no preamble."

SPEC = PromptSpec(
    id="audit.spec_brief",
    version="1.0.0",
    blocks=(
        Block("role", _ROLE),
        Block("instructions", _INSTRUCTIONS),
        Block("footer", _FOOTER),
    ),
)

register(SPEC)
