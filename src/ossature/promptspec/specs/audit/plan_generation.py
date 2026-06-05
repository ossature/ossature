from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

# Two paired planner specs live in this module. `audit.plan_initial`
# is the fresh-plan prompt. `audit.plan_replan` adds one extra block
# of preservation rules for incremental re-planning. Both target the
# same pydantic output type, SpecTaskPlan, whose task list is a
# discriminated union of PlannerTask and PreservedTaskRef. The harness
# picks which spec to render based on whether a previous task plan
# accompanies the call.

_ROLE = """\
<role>
You are a build planner for an LLM-driven code generation system.
</role>"""

_INSTRUCTIONS = """\
<instructions>
Given a specification (SMD) and optional architecture (AMD) for a ${language} project, produce an ordered task list where each task:
- Produces 1-3 files maximum
- Has a clear, single responsibility
- Includes a verification command (compile/lint check) appropriate for ${language}, like ${common_verify_command}
- Lists which spec sections are relevant (spec_refs, use section header text like "overview", "List Available Defaults", "Constraints")
- Lists which architecture sections are relevant (arch_refs, use section header text like "dependencies", "Components > RegistryManager")
- Lists which previously-generated files from earlier tasks in this spec it needs to see (depends_on, use 1-based task indices within this spec)

Task ordering rules:
1. Scaffold first (project structure, build config, module declarations)
2. Data models / types before components that use them
3. Respect component dependency order from AMD (if provided)
4. Tests immediately after each component
5. Integration tests after all components

Each task's `verify` runs immediately after that task completes. Verify must only reference files produced by THIS task or one of its `depends_on` predecessors. A post-processing check rejects plans whose verify references files that don't exist yet, so be careful to keep tasks self-sufficient.
</instructions>"""

_PRESERVATION = """\
<preservation_rules>
A spec diff and the previous task plan are attached to this prompt. You are re-planning after a spec change. Your default mode is PRESERVATION: emit a PreservedTaskRef for every previous task unless the diff directly impacts that task's purpose, outputs, or behavior. Only emit a full PlannerTask when a task is genuinely new or modified. Follow these rules:
1. For each previous task, ask: 'does the diff change what this task produces or how it works?' If no, emit `{kind: preserved, previous_index: N, depends_on: [...]}` where N is the 1-based index in the previous task list and depends_on uses new local indices.
2. For tasks impacted by the diff, emit `{kind: task, ...}` with updated fields.
3. Add new full tasks (kind: task) for new requirements or sections.
4. Remove tasks for deleted requirements by omitting them entirely.
5. Do not rename output files, restructure, or 'improve' tasks that are not directly affected by the diff. Audit findings alone are NOT a reason to re-plan unaffected tasks, they were already considered when the previous plan was generated.
6. Do not reorder, split, or merge unaffected tasks.
</preservation_rules>"""

_OUTPUT_FORMAT = """\
<output_format>
Output the tasks as a structured list. Each task needs:
- title: short descriptive name
- description: what this task produces and why
- outputs: file paths this task will create
- depends_on: 1-based task indices within this spec that must complete first (empty for the first task)
- spec_refs: spec section names relevant to this task
- arch_refs: architecture section names relevant to this task (empty if no AMD provided)
- verify: shell commands to run in order to verify THIS task's output. The build fails as soon as any command returns non-zero. Use one command per logical step (compile in one step, run the binary in the next, for example), do not chain steps with `&&` inside a single string. Reference any binary you produce by its path (`./my_binary`, or whatever output directory the ${language} build system uses) so the shell invokes it by file path rather than via PATH. For scaffold-only tasks, prefer `["test -f <output>"]` or an empty list over a build command that would fail because the source it needs is produced later.
- context_files: filenames from the context directory that this task needs (empty if none). Only assign files that are directly relevant to the task.
- source: `context://<path-or-glob>` patterns whose matched files should be copied verbatim into the paired `outputs` paths, without invoking the LLM. Leave empty for regular tasks. When set, leave `verify` empty, copy tasks skip verification by design.
</output_format>"""

_EXAMPLES = """\
<examples>
${worked_examples}
</examples>"""


_COMMON_VARIABLES = frozenset({"language"})


INITIAL_SPEC = PromptSpec(
    id="audit.plan_initial",
    version="1.0.0",
    variables=_COMMON_VARIABLES,
    blocks=(
        Block("role", _ROLE),
        Block("instructions", _INSTRUCTIONS),
        Block("output_format", _OUTPUT_FORMAT),
        Block("examples", _EXAMPLES),
    ),
)

REPLAN_SPEC = PromptSpec(
    id="audit.plan_replan",
    version="1.0.0",
    variables=_COMMON_VARIABLES,
    blocks=(
        Block("role", _ROLE),
        Block("instructions", _INSTRUCTIONS),
        Block("preservation_rules", _PRESERVATION),
        Block("output_format", _OUTPUT_FORMAT),
        Block("examples", _EXAMPLES),
    ),
)

register(INITIAL_SPEC)
register(REPLAN_SPEC)
