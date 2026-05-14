# 5. Build and Iterate

Previous step: [Review the plan](04-reviewing-the-plan.md). This is the last step of the markman tutorial.

## Build

When the plan looks right:

```bash
ossature build
```

For each task, Ossature assembles a prompt from the project brief, relevant spec sections, interface files, and output from earlier tasks. The LLM generates code and writes files to the output directory. After each task, the verify command runs. If verification fails, a separate fixer agent reads the errors and tries to repair the code, up to `max_fix_attempts` times (default 3).

By default the build continues silently on success and pauses on failure with a prompt: retry, skip, or quit. Other modes:

```bash
ossature build --step    # pause after every task for approval
ossature build --auto    # run to completion, stop on first failure
ossature build --auto --skip-failures  # run everything, skip failures
```

For a first build, `--step` is useful so you can inspect the output before continuing.

### If something fails

`ossature retry` re-runs failed tasks:

```bash
ossature retry                # re-run all failed tasks
ossature retry --from 007     # redo everything from task 007 onwards
ossature retry --only 005     # re-run task 005 and all its dependents
```

Check progress at any point with `ossature status`.

See [How the Build Works](../topics/how-the-build-works.md) for the full details on the build loop, fix loop, invalidation, and retry.

## Iterate

After a build, you'll usually want to change something. Edit the spec, then run the same sequence again:

```bash
ossature validate
ossature audit
ossature build
```

The build is incremental. If you change `storage.smd`, only STORAGE tasks and any downstream tasks that reference STORAGE's interface get rebuilt. CLI and WEBUI tasks stay untouched if STORAGE's public interface didn't change.

## Summary

```
ossature init       create project
edit specs          describe what to build
ossature validate   fix structural issues (loop until clean)
ossature audit      LLM review + plan generation (fix errors, loop until clean)
review plan.toml    check task order, granularity, verify commands
ossature build      generate code task by task
ossature retry      re-run failures
edit specs          iterate
```

The specs are your source of truth. The plan is your review checkpoint. When something breaks, you fix the spec or the plan and rebuild instead of starting over.

## Next Steps

- [SMD Format](../reference/smd-format.md) - Full spec format reference
- [AMD Format](../reference/amd-format.md) - Architecture format reference
- [Configuration](../reference/configuration.md) - All config options
- [Commands](../reference/cli.md) - CLI reference
- [How the Build Works](../topics/how-the-build-works.md) - Build loop, invalidation, retry internals
