# Interfaces and Boundaries

A project with multiple specs is not just a collection of specs that happen to share a directory. The `depends` field in each SMD creates a directed acyclic graph, and that graph controls something important: where the LLM's view of each spec ends and where the public contract of its dependencies begins.

## The dependency graph

When `api.smd` declares `depends: [AUTH, DATABASE]`, it means the API spec assumes auth and database are already implemented. The graph controls build order and scopes what the LLM sees when generating each spec's code.

Spec-level dependencies (`depends`) are different from component-level dependencies inside an AMD. Spec dependencies mean "this spec's requirements assume the other spec is implemented." Component dependencies control the order of tasks within a single spec.

A concrete example:

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

AUTH and DATABASE have no dependencies and can be built first. API depends on both, so it comes after. FRONTEND depends on API, so it is last.

## Interface extraction

After all tasks for a spec complete, Ossature extracts the public interface from the generated code: types, function signatures, error types. This gets written to `.ossature/context/interfaces/{spec_id}.md`.

When building downstream specs, the LLM sees these interface files rather than the full implementation. This boundary is intentional and has two consequences.

First, it limits cascade rebuilds. If you change the internals of the auth module without changing its public types or function signatures, API tasks do not need to rebuild. The interface file does not change, so those tasks' input hashes stay the same. Only when the public surface actually changes does the invalidation propagate downstream.

Second, it makes the generated code for downstream specs more stable. The LLM generates code against a defined contract rather than against implementation details that might shift between builds. Each spec is a consumer of its dependencies' public API, not of their implementation.

This is the same principle as header files in C. Change the `.c` without changing the `.h` and consumers do not recompile.

## Incremental re-planning across specs

When you change one spec in a multi-spec project and re-run `ossature audit`, only that spec's tasks get regenerated. The planner sees a diff of what changed and the previous task plan, so it preserves unaffected tasks rather than starting from scratch. Tasks for unchanged specs are preserved with their existing hashes and statuses.

Within the changed spec, tasks that produce the same output files as before carry over their build status and state. A typo fix or a minor tweak to one requirement does not throw away progress on the rest of the spec. Stale output files from tasks dropped during the re-plan are automatically removed.

## Build ordering

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

Tasks from specs at the same level are independent of each other. Within a level, specs could in principle be built in parallel, though the current implementation processes them sequentially.
