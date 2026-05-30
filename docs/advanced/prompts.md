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

## Language profiles

Some prompts need more than just the language name. The planner
prompt, for example, has to mention build-invocation commands the
scaffold rule forbids, typical manifest filenames, and worked task
examples in that language. Hand-coding those into the prompt body
forces the model to filter the noise at inference time and risks
leaking unrelated language tooling into the output.

A LanguageProfile holds the per-language data the prompts need: a
setup-command example, scaffold manifest names, build-invocation
examples, a safe verify-examples paragraph, a common verify command,
and a block of worked task examples. Curated profiles ship for
python, rust, javascript, typescript, lua, and zig under
`src/ossature/promptspec/profiles/`. TypeScript is split from
JavaScript because the tooling diverges (tsc, tsconfig, type-only
verify) even though both run on the npm/node toolchain. Anything else
falls through to a generic profile whose field values use directive
wording (look at the manifest, prefer single-file checks) and
interpolate the language name where needed, so `language = "elixir"`
keeps working with weaker but still useful guidance.

When a spec declares `language` as a variable, the renderer pulls the
active profile's fields into the substitution namespace. A prompt
template can then write `${build_invocation_examples}`,
`${scaffold_manifests}`, `${worked_examples}`, and the like alongside
`${language}`, and the renderer fills each from the resolved profile.

Adding a new curated language is a single-file change. Drop a new
module under `profiles/`, fill in the LanguageProfile dataclass, and
register it. No prompts need editing.

## Behavior parity

Most prompts only use the language name and are byte-equivalent to
the original `Final[str].format(language=...)` output. The planner
prompt is different because Ticket 2 deliberately rewrote it to use
profile injection. A parametrized snapshot test in
`tests/unit/test_promptspec_snapshots.py` pins the current rendered
output for each prompt against a fixture, and a dedicated test in
`tests/unit/test_language_profiles.py` asserts the cross-language
guarantee that a render targeted at one language never mentions
another curated language's tooling.
