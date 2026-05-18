# How Validation Works

Ossature's pipeline has three phases: validate, audit, build. Validation comes first and is purely structural. It parses every spec file and checks the shape of the content without invoking an LLM. This separation matters because it catches the class of problems that have nothing to do with what the code should do and everything to do with whether the spec itself is well-formed.

The two file formats that validation checks are SMD (Spec Markdown) and AMD (Architecture Markdown).

| Format | Extension | Purpose |
|--------|-----------|---------|
| SMD | `.smd` | Defines what the system should do |
| AMD | `.amd` | Defines how it should be structured |

SMD is required. AMD is optional. If you omit the AMD, the LLM infers the architecture during the audit phase based on what's in your spec.

## How SMD and AMD relate

Each AMD links back to its parent SMD via the `spec` field in its frontmatter. A single SMD can have multiple AMDs describing different parts of the system. A database spec might have one AMD for the models layer and another for migrations.

```
specs/
├── auth.smd                    # What auth should do
│   └── auth.amd                # How auth is structured
├── database.smd                # What the database layer does
│   ├── database-models.amd     # Just the models
│   └── database-migrations.amd # Just the migrations
└── api.smd                     # What the API does
    └── api.amd                 # How the API is structured
```

Multiple AMDs for the same spec are additive. Their component lists, data models, and dependencies get merged.

## The dependency graph

SMD files form a directed acyclic graph through their `depends` field. When `api.smd` declares `depends: [AUTH, DATABASE]`, it means the API spec assumes auth and database are already implemented.

This is different from component-level dependencies inside an AMD. Spec dependencies control the order that specs get planned and built. Component dependencies control the order of tasks within a single spec.

## What the parser checks in each SMD

Every SMD file must have:

- A YAML frontmatter block with `id`, `status`, and `priority` fields using valid values
- An H1 title
- A non-empty `## Overview` section
- At least one bullet in each of `## Goals`, `## Non-Goals`, `## Constraints`, and `## Acceptance Criteria`
- At least one requirement under `## Requirements`, where each requirement must have a `**Accepts:**` field, a `**Returns:**` field, and a description before the first bold field
- At least one example under `## Examples`, where each example must have `**Input:**` and `**Output:**` markers followed by code blocks
- Any `**Errors:**` bullet in a requirement must use a `->` or `->` separator between condition and message

## Cross-reference checks

After parsing individual files, the cross-reference pass checks:

- Every `depends` target in an SMD frontmatter resolves to a real spec ID
- No cycles exist in the dependency graph
- Every `spec` reference in an AMD frontmatter resolves to a real SMD

## Complexity warning

If a spec's combined requirement text exceeds a length threshold, validation prints a warning suggesting you consider splitting it into multiple specs. Complex specs can overload the planner and produce poorly-scoped task plans.

## Why validate before auditing

Running a structural check before sending specs to the LLM is cheap. A broken `depends` reference, a missing required section, or a requirement without Accepts/Returns are problems the LLM cannot fix and that would corrupt the plan regardless of how well the semantic review went. Catching them in a fast, local check avoids burning LLM tokens on specs that cannot yet be built.

For details on what each field should contain, see [SMD Format](../reference/smd-format.md), [AMD Format](../reference/amd-format.md), and [`ossature validate`](../reference/cli.md#ossature-validate) in the CLI reference.
