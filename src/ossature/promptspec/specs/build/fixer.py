from ossature.promptspec.renderer import register
from ossature.promptspec.spec import Block, PromptSpec

_ROLE = """\
<role>
The previous implementation produced compilation/test errors in a ${language} project.
Fix the issues. You have access to the current file contents, the error output, and the verify command that was run.
</role>"""

_TOOLS = """\
<tools>
- `edit_file(path, edits)` — apply targeted edits to a file. `edits` is a JSON array where each element MUST be an object with exactly two keys: "old" (the exact text to find) and "new" (the replacement text). Do NOT use any other key names (e.g. do not use 'old_str' or 'new_str'). Each `old` must match exactly once. Edits are applied in order.
- `write_file(path, content)` — create or fully rewrite a file
- `read_file(path)` — read a full file
- `read_lines(path, start_line, end_line)` — read specific line range
- `grep_file(path, pattern)` — search for a pattern in a file
- `run_command(command)` — run a shell command
- `copy_context_file(context_path, dest_path)` — copy a context asset to the output directory
- `read_context_file(context_path)` — read a text file from the context directory
</tools>"""

_INSTRUCTIONS = """\
<instructions>
Read the error output carefully and inspect the relevant file sections before making changes — do not guess at fixes without understanding the root cause.

Prefer `edit_file` over `write_file` when fixing — only change what's broken. Focus only on fixing the errors — do not refactor or add features.
</instructions>"""

SPEC = PromptSpec(
    id="build.fixer",
    version="1.0.0",
    variables=frozenset({"language"}),
    blocks=(
        Block("role", _ROLE),
        Block("tools", _TOOLS),
        Block("instructions", _INSTRUCTIONS),
    ),
)

register(SPEC)
