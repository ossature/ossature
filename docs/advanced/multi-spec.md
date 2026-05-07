# Multi-Spec Projects

A project can have any number of spec files that depend on each other. The `depends` field in each SMD creates a directed acyclic graph between specs.

## Example Structure

```
specs/
├── auth.smd                    # depends: []
│   └── auth.amd
├── database.smd                # depends: []
│   ├── database-models.amd
│   └── database-migrations.amd
├── api.smd                     # depends: [AUTH, DATABASE]
│   └── api.amd
└── frontend.smd                # depends: [API]
```

Here, AUTH and DATABASE have no dependencies and can be built first. API depends on both, so it comes after. FRONTEND depends on API, so it's last.

## How Dependencies Work

Spec-level dependencies (`depends`) are different from component-level dependencies (inside an AMD). Spec dependencies mean "this spec's requirements assume the other spec is implemented." Component dependencies control the order of tasks within a single spec.

During `ossature audit`, the planner generates tasks for each spec and orders them by the dependency graph. Tasks for AUTH and DATABASE come before tasks for API. Tasks for API come before tasks for FRONTEND.

## Interface Boundaries

After all tasks for a spec complete, Ossature extracts the public interface from the generated code: types, function signatures, error types. This gets written to `.ossature/context/interfaces/{spec_id}.md`.

When building downstream specs, the LLM sees these interface files instead of the full implementation. This means:

- If you change auth internals without changing its public interface, API tasks don't need to rebuild
- The LLM generates code against stable contracts rather than implementation details
- Changes cascade only when the public surface actually changes

This works the same way header files work in C. Change the `.c` without changing the `.h` and consumers don't recompile.

## Build Order

The spec graph gets serialized to `.ossature/graph.toml`:

```toml
[[spec]]
id = "AUTH"
file = "./specs/auth.smd"
depends = []
architectures = ["./specs/auth.amd"]

[[spec]]
id = "DATABASE"
file = "./specs/database.smd"
depends = []
architectures = ["./specs/database-models.amd",
                  "./specs/database-migrations.amd"]

[[spec]]
id = "API"
file = "./specs/api.smd"
depends = ["AUTH", "DATABASE"]
architectures = ["./specs/api.amd"]

[order]
levels = [
    ["AUTH", "DATABASE"],   # Level 0: no dependencies
    ["API"],                # Level 1: depends on level 0
    ["FRONTEND"],           # Level 2: depends on level 1
]
```

## Incremental Re-Planning

When you change only one spec in a multi-spec project and re-run `ossature audit`, Ossature performs an incremental re-plan. Only the changed spec's tasks are regenerated. The planner sees a diff of what changed and the previous task plan, so it preserves unaffected tasks rather than planning from scratch. Tasks for unchanged specs are preserved with their existing hashes and statuses.

Within the changed spec, tasks that produce the same output files as before carry over their build status and state. This means a typo fix or a minor tweak to one requirement won't throw away progress on the rest of the spec.

Stale output files from tasks that were dropped during the re-plan are automatically removed.

## Building a Single Spec

Use `--spec` to build just one spec and its transitive dependencies:

```bash
ossature build --spec API
```

This builds AUTH, DATABASE, and API tasks but skips FRONTEND.
