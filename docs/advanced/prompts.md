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

## System prompt vs user prompt

The PromptSpec is the system prompt, rendered once and reused across
calls. Context-specific instructions belong in the user prompt
alongside the data they refer to, not in the system prompt as a
conditional ("if X is provided, do Y"). For the planner, this means
the setup-command instruction, audit-findings instruction, and
verbatim-copy-tasks explanation all live in `audit/planner.py`'s
user-prompt assembly, gated by `if config.build.setup`, `if
audit_report.findings`, and `if context_inventory`. The system prompt
stays a stable core, which keeps the rendered text shorter when those
contexts don't apply and helps prefix-based prompt caching land
better hit rates.

## Paired specs for the planner

There are two planner specs, `audit.plan_initial` for fresh planning
and `audit.plan_replan` for re-planning after a spec change. Both
share their role, instructions, output-format, and examples blocks.
`plan_replan` also includes a `preservation_rules` block that tells
the model to emit a PreservedTaskRef for previous tasks the diff
doesn't touch.

Routing happens in `audit/planner.py`. When the call carries both a
spec diff and a previous task list, the planner renders the re-plan
spec. Otherwise it renders the initial spec. Both target the same
pydantic output type, `SpecTaskPlan`, whose task list is a
discriminated union of `PlannerTask` and `PreservedTaskRef`.

## Verify validator

After the planner generates a `SpecTaskPlan`, a post-processing
validator walks each task's verify commands and flags any that would
fail because the source they need doesn't exist yet at that point in
the plan. The planner prompt also states this rule in prose, telling
the model that a task's verify may only reference files produced by
that task or one of its `depends_on` predecessors, and that
scaffold-only tasks should prefer a `test -f` check or an empty list
over a build command whose source comes later. The validator runs the
same check after generation. When the validator finds a problem, it raises
`ModelRetry`, which feeds the error message back through the agent
loop and asks the LLM to fix the affected tasks.

The validator's logic is language-agnostic. It asks the active
`LanguageProfile` two questions per verify command: is this a build
invocation, and does some task in the chain produce a source file. A
profile answers using three tuple fields: `build_invocation_tokens`
(substrings like `"cargo build"` or `"npm install"`),
`source_extensions` (file extensions that count as compilable source,
like `".rs"` or `".py"`), and `manifest_filenames` (basenames that
share an extension with source but act as manifests, like
`"build.zig"` and `"conf.lua"`). Empty tuples disable the check, which
is how the generic profile and any unknown language avoid false
positives.

The validator adds no extra place to edit for a curated language. The
three new tuples sit alongside the prompt-facing string fields in the
same LanguageProfile dataclass.

## Language profiles

Some prompts need more than just the language name. The planner
prompt, for example, has to mention build-invocation commands the
scaffold rule forbids, typical manifest filenames, and worked task
examples in that language. Hand-coding those into the prompt body
forces the model to filter the noise at inference time and risks
leaking unrelated language tooling into the output.

A LanguageProfile carries the per-language data the prompts need.
It has a setup-command example, scaffold manifest names,
build-invocation examples, a safe verify-examples paragraph, a
common verify command, and a block of worked task examples. Curated
profiles live under `src/ossature/promptspec/profiles/` for python,
rust, javascript, typescript, lua, and zig. TypeScript is split from
JavaScript because the tooling diverges (tsc, tsconfig, type-only
verify) even though both run on the npm/node toolchain. Anything
else falls through to a generic profile whose field values use
directive wording (look at the manifest, prefer single-file checks)
and interpolate the language name where needed, so
`language = "elixir"` keeps working with weaker but still useful
guidance.

When a spec declares `language` as a variable, the renderer pulls the
active profile's fields into the substitution namespace. A prompt
template can then write `${build_invocation_examples}`,
`${scaffold_manifests}`, `${worked_examples}`, and the like alongside
`${language}`, and the renderer fills each from the resolved profile.

Adding a new curated language touches two files. Drop a new module
under `profiles/` that fills in the LanguageProfile dataclass and calls
`register_profile`, then add an import for it to `profiles/__init__.py`
so the module loads and registers at import time. No prompts need
editing.

## Snapshot coverage

Every prompt has fixtures under `tests/unit/fixtures/promptspec/`,
capturing the rendered output for each language-bearing spec across
each curated language plus a fallback case that exercises the
generic profile. A parametrized snapshot test in
`tests/unit/test_promptspec_snapshots.py` re-renders each spec and
compares against its fixture. A dedicated test in
`tests/unit/test_language_profiles.py` enforces the cross-language
guarantee that a render targeted at one language never mentions
another curated language's tooling.
