# Prompts

The system prompts that drive Ossature's LLM calls are declared as
PromptSpecs. A PromptSpec is a structured, versioned object that the
harness renders into a final string at call time, rather than a free
form template.

Each PromptSpec carries a stable id like `audit.spec_audit` or
`build.implementer`, a semantic version, and an ordered list of named
blocks (role, instructions, output_format, examples, tools, workflow,
and so on). Each block's content is a literal string that includes its
own XML wrapping tags, so a future variant override can swap the
block wholesale without the renderer having to reconstruct the tags.
The `variables` field is the declared set of placeholders. Today the
only one in use is `language`. Substitution uses `string.Template`
syntax (`${language}`).

The renderer is a pure function. Given a spec id and variable values
it joins the blocks with a blank line, substitutes the variables, and
returns the result.

PromptSpecs live under `src/ossature/promptspec/`. The `spec.py` module
holds the Block and PromptSpec dataclasses, `renderer.py` holds the
registry and `render()`, and the per-prompt modules live under
`specs/audit/` and `specs/build/`. Each spec module calls `register(...)`
at import time, so importing `ossature.promptspec` is enough to
populate the registry.

To inspect a rendered prompt locally:

```python
from ossature.promptspec import render, registered_ids

print(registered_ids())
# ['audit.cross_spec_audit', 'audit.interface_inference', ...]

print(render("audit.spec_audit", language="python"))
# <role>
# You are a senior technical reviewer ...
```

If you pass an unknown variable, omit a declared one, or use an
unregistered id, `PromptSpecError` is raised so the failure mode is
loud rather than silent.

The port preserves the final string the LLM receives. A parametrized
snapshot test in `tests/unit/test_promptspec_snapshots.py` asserts
that for every shipped prompt, `render(...)` byte-matches a fixture
captured from the previous `Final[str].format(language=...)` output.
