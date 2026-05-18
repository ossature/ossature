# Tutorials

Tutorials are step-by-step lessons that walk through complete, runnable scenarios. Each one starts from zero and ends with working generated code. Read them in order; each step builds on the previous one.

If you get stuck at any point, the relevant reference pages are linked from each step, and the how-to guides cover specific recovery scenarios.

## Available tutorials

**[1. Initialize the Project](01-init-and-config.md)**
Create the project directory and write the initial configuration.

**[2. Write Your Specs](02-writing-specs.md)**
Plan the module breakdown, create the SMD files, and write requirements.

**[3. Validate and Audit](03-validate-and-audit.md)**
Check your specs for structural issues and send them to the LLM for semantic review.

**[4. Review the Plan](04-reviewing-the-plan.md)**
Read through the generated build plan and adjust it before building.

**[5. Build and Iterate](05-build-and-iterate.md)**
Run the build, handle failures, and iterate after editing specs.

The tutorial uses [markman](https://github.com/ossature/ossature-examples/tree/master/markman), a Rust bookmark manager with a CLI and a read-only web UI, as its running example.
