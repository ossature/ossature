# The .ossature/ Directory

All Ossature state lives in `.ossature/`. Here's what's inside after an audit and build:

```
.ossature/
├── manifest.toml              # Source file checksums and brief input hashes
├── graph.toml                 # Resolved spec dependency graph
├── audit-report.md            # Audit findings across all specs
├── plan.toml                  # The build plan (editable)
├── state.toml                 # Per-task input/output hashes
├── audits/
│   ├── EXPENSE_TRACKER/
│   │   ├── prompt.md          # Exact prompt sent to the auditor
│   │   └── response.json      # Cached per-spec audit findings
│   └── cross-spec/
│       ├── prompt.md          # Exact prompt sent to the cross-spec auditor
│       └── response.json      # Cached cross-spec audit findings
├── planners/
│   └── EXPENSE_TRACKER/
│       ├── prompt.md          # Exact prompt sent to the planner
│       └── response.json      # Raw per-spec task plan from the LLM
├── snapshots/
│   └── EXPENSE_TRACKER.md     # Rendered spec content for diffing
├── context/
│   ├── project-brief.md       # Project summary for LLM context
│   ├── spec-briefs/
│   │   └── EXPENSE_TRACKER.md # Per-spec summary
│   └── interfaces/
│       └── EXPENSE_TRACKER.md # Public interface signatures
└── tasks/
    ├── 001-project-scaffold/
    │   ├── prompt.md           # Exact prompt sent to LLM
    │   ├── response.md         # LLM's raw response
    │   └── output.toml         # Files written, verification result
    ├── 002-storage-layer/
    │   └── ...
    └── ...
```

Every prompt and response is saved in per-task directories. If something goes wrong at task 14, you can read `tasks/014-*/prompt.md` and `response.md` to see exactly what the LLM was asked and what it produced.
