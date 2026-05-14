# Ossature

!!! warning "Unstable - v0.x"

    Ossature is currently in its `0.x` series and should be considered **unstable**.
    APIs, spec formats, CLI flags, and internal behavior may change significantly
    between releases without prior deprecation. Pin your version and check the
    [changelog](https://github.com/ossature/ossature/blob/master/CHANGELOG.md)
    before upgrading.

**An open-source harness for spec-driven code generation.**

You write specifications describing what your software should do, optionally lay out the architecture, and Ossature breaks it down into a build plan that gets executed step by step, with an LLM generating the code under tight constraints.

This is not "throw a prompt at a model and hope for the best." The specs are your source of truth. You review the plan before anything gets built. When something breaks at step 14 of 30, you fix that step and keep going instead of starting over. When requirements change, you update the spec and Ossature figures out what needs to be regenerated.

## How It Works

A project is a collection of spec files (`.smd`) and optional architecture files (`.amd`). Ossature discovers them, builds a dependency graph, and processes them through three stages:

```
ossature validate → ossature audit → ossature build
```

**Validate** parses your specs and checks that everything is structurally sound. No LLM involved.

**Audit** sends your specs to an LLM for review, generates context files (briefs, interfaces) and a build plan. The plan is a TOML file you can read and edit before anything gets built.

**Build** executes the plan task by task, calling the LLM to generate code for each one. Each task produces a small number of files, gets verified, and if verification fails there's a fix loop that tries to repair it automatically.

All state lives in a `.ossature/` directory. Builds are incremental - if you change a spec, only the affected tasks get rebuilt. Interface files act as boundaries between specs, so internal changes that don't affect the public surface don't cascade to downstream specs.

## Next Steps

- **[First Steps](first-steps/installation.md)** - Install Ossature and run your first project
- **[Tutorials](tutorials/index.md)** - Step-by-step lessons for a complete project
- **[Topics](topics/index.md)** - Background reading on how Ossature works
- **[How-to](how-to/index.md)** - Recipes for specific tasks
- **[Reference](reference/index.md)** - CLI commands, file formats, and configuration
