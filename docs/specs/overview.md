# Specification Formats

Ossature uses two Markdown-based formats to describe your project:

| Format | Extension | Purpose |
|--------|-----------|---------|
| **SMD** (Spec Markdown) | `.smd` | Define *what* the system should do |
| **AMD** (Architecture Markdown) | `.amd` | Define *how* it should be structured |

SMD is required. AMD is optional. If you skip the AMD, the LLM will infer the architecture during the audit phase based on what's in your spec.

## How They Relate

Each AMD links back to its parent SMD via the `@spec` field. A single SMD can have multiple AMDs describing different parts of the system. For example, a database spec might have one AMD for the models layer and another for migrations.

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

Multiple AMDs for the same spec are additive. Their component lists, data models, and dependencies get merged. If two AMDs define the same component name, that's a validation error.

## The Dependency Graph

SMD files form a directed acyclic graph through their `@depends` field. When `api.smd` declares `@depends: [AUTH, DATABASE]`, it means the API spec assumes auth and database are already implemented.

This is different from component-level dependencies inside an AMD. Spec dependencies control the order that specs get planned and built. Component dependencies control the order of tasks within a single spec.

## Validation

`ossature validate` checks both formats:

- Each file parses correctly
- All `@depends` targets exist
- All `@spec` references in AMDs resolve to real SMDs
- No duplicate component names across AMDs for the same spec
- No cycles in the dependency graph

This is purely structural. No LLM calls.

## Next Steps

- [SMD Format](smd.md) - Full spec format reference
- [AMD Format](amd.md) - Architecture format reference
