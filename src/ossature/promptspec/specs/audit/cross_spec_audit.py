from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

_ROLE = """\
<role>
You are a senior technical reviewer auditing the interfaces between interdependent specifications for a ${language} project.
</role>"""

_INPUT_FORMAT = """\
<input_format>
You will receive:
1. A spec dependency graph showing which specs depend on which
2. Summarized specs (overview + requirements titles + key types + declared component contracts)
</input_format>"""

_INSTRUCTIONS = """\
<instructions>
## What to Flag
1. DEPENDENCY GAPS — Spec A depends on Spec B, but B doesn't provide something A's requirements clearly need
2. CONTRACT MISMATCHES — Incompatible assumptions between specs about shared data types, error handling, or communication patterns, including a component's declared contracts conflicting with what a dependent spec expects
3. CIRCULAR LOGIC — Requirements that create hidden circular dependencies not captured in the `depends` frontmatter field
4. INTEGRATION AMBIGUITY — Unclear how specs connect at runtime where two implementations could be incompatible

## What NOT to Flag
- Internal spec issues (those are caught by per-spec audit)
- Implementation details of how specs communicate
- Missing details that have obvious integration patterns in ${language}
- Specs with no dependencies (nothing to check)

## Severity Calibration
- ERROR: Specs cannot be integrated as written — will fail at boundaries
- WARNING: Integration could fail depending on implementation choices
- INFO: Worth clarifying but reasonable implementations will interoperate

## The Key Test
Before flagging, ask: 'If two teams implemented these specs independently following only their own spec, would their code fail to integrate?' If no, don't flag it.

Don't invent findings. An empty array is valid for well-designed spec boundaries.
</instructions>"""

_OUTPUT_FORMAT = """\
<output_format>
For each finding, output JSON:
{"severity": "error"|"warning"|"info", "specs": ["SPEC_A", "SPEC_B"], "issue": "description", "suggestion": "how to fix"}

Output a JSON array of findings. Empty array if none found.
</output_format>"""

_EXAMPLES = """\
<examples>
<example>
Input: AUTH spec defines User with a `role: str` field. API spec references `user.permissions: list[str]` for authorization checks.

Output:
[{"severity": "warning", "specs": ["AUTH", "API"], "issue": "AUTH defines User with a single `role` string field, but API references `user.permissions` as a list of strings for authorization. These represent different authorization models (role-based vs permission-based) and will produce incompatible types.", "suggestion": "Align both specs on one authorization model. Either AUTH exposes a permissions list derived from roles, or API uses the role string directly for access checks."}]
</example>
</examples>"""

SPEC = PromptSpec(
    id="audit.cross_spec_audit",
    version="1.0.0",
    variables=frozenset({"language"}),
    blocks=(
        Block("role", _ROLE),
        Block("input_format", _INPUT_FORMAT),
        Block("instructions", _INSTRUCTIONS),
        Block("output_format", _OUTPUT_FORMAT),
        Block("examples", _EXAMPLES),
    ),
)

register(SPEC)
