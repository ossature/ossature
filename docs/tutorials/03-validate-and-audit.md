# 3. Validate and Audit

**What you'll do:** Confirm that your specs are structurally correct, send them to the LLM for semantic review, and review the findings before building.

Previous step: [Write your specs](02-writing-specs.md). Next step: [Review the plan](04-reviewing-the-plan.md).

## Validate

Check that your specs are structurally correct:

```bash
ossature validate
```

This parses every `.smd` and `.amd` file and checks that all `depends` targets exist, all `spec` references in AMDs resolve to real SMDs, there are no duplicate component names within a spec, and there are no cycles in the dependency graph. No LLM is involved.

You should see a green checkmark followed by a summary table listing each spec ID, its status, priority, number of requirements, and dependencies. If any spec is missing a required section or has a structural error, validation exits with a specific error message naming the problem and the file.

!!! tip "If something goes wrong"
    Common parse errors include: a `depends` target that doesn't match any `id`; a requirement missing `**Accepts:**` or `**Returns:**`; an example missing `**Input:**` or `**Output:**` markers; a Goals, Non-Goals, Constraints, or Acceptance Criteria section with no bullet items. Fix each error and re-run until validation passes clean.

    If a spec's requirements are very long, validate will also warn that the spec may be too complex for the planner and suggest splitting it.

## Audit

Audit sends your specs to the LLM for semantic review and generates the build plan:

```bash
ossature audit
```

It runs through several stages: computing checksums and checking what changed since the last audit, reviewing each changed spec for ambiguity, contradictions, gaps, and feasibility issues, running a cross-spec audit if there are multiple specs, generating a project brief and per-spec briefs, extracting or inferring interface signatures, and finally generating the build plan.

You should see a progress summary as each stage completes, ending with a line reporting where the plan was written.

### Reviewing findings

The audit produces findings at three severity levels: errors (will likely cause build failures, fix these), warnings (potential problems worth considering), and info (observations you can usually ignore).

For example, auditing the markman storage spec produced a warning about an inconsistency between the example output and the requirement text:

!!! warning "Ambiguous timestamp format"
    The spec shows `created_at` in the example output as ISO format (`2026-01-01T00:00:00`), but the requirement states it is stored as `YYYY-MM-DD HH:MM:SS` (space-separated, as produced by SQLite `datetime('now')`). This could cause implementations to differ in timestamp format.

The example and the requirement disagreed on the format. The fix was to update the example output to use the space-separated format that SQLite actually produces:

````markdown
**Output:**

```
[Bookmark { id: 1, url: "https://example.com", desc: "Example site",
tags: "example,test", created_at: "2026-01-01 00:00:00" }]
```
````

By default, the audit auto-fixes errors without prompting -- it edits your spec files directly, re-audits, and repeats up to 3 cycles per spec. Warnings and info are reported but not auto-fixed. Use `--interactive` if you want to approve each fix, or `--no-fix` to skip fixing entirely. After audit completes, all findings are saved to `.ossature/audit-report.md`.

!!! tip "If something goes wrong"
    If audit exits with a rate limit error, wait a minute and run it again. If it fails with an authentication error, check that your API key environment variable is set and matches the provider in your `model` string.

### Incremental audits

On subsequent runs, audit only re-processes specs whose files have changed. If you edit `storage.smd` but leave `cli.smd` and `webui.smd` untouched, only STORAGE gets re-audited and re-planned. Tasks for CLI and WEBUI are preserved entirely. See [How the Build Works](../topics/how-the-build-works.md#incremental-re-planning) for details.

## Next step

[Review the plan](04-reviewing-the-plan.md) -- Read through the generated build plan and check that the task breakdown looks right before building.
