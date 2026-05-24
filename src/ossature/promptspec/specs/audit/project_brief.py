from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

_ROLE = """\
<role>
You are a technical writer creating a project summary for an LLM code generation system.
</role>"""

_INSTRUCTIONS = """\
<instructions>
Given the overview sections of all specs in a project, write a single paragraph (~200 words) that captures:
- What the project does
- The main modules/specs and their responsibilities
- Key technologies and frameworks
- How the modules connect

Write in present tense, be concrete, avoid marketing language.
This summary will be included in every code generation prompt to provide project context.
</instructions>"""

_FOOTER = "Output only the brief, no preamble."

SPEC = PromptSpec(
    id="audit.project_brief",
    version="1.0.0",
    blocks=(
        Block("role", _ROLE),
        Block("instructions", _INSTRUCTIONS),
        Block("footer", _FOOTER),
    ),
)

register(SPEC)
