# 1. Initialize the Project

This is the first step of the multi-page tutorial that walks through a full project from init to generated code using [markman](https://github.com/ossature/ossature-examples/tree/master/markman), a Rust bookmark manager with a CLI and a read-only web UI. Next: [Write your specs](02-writing-specs.md).

```bash
ossature init markman
cd markman
```

This creates an `ossature.toml` and a `specs/` directory. Open the config and set it up:

```toml
[project]
name = "markman"
version = "0.0.1"
spec_dir = "specs"

[output]
dir = "output"
language = "rust"

[build]
setup = ["cargo init --name markman"]
verify = ["cargo check"]

[llm]
model = "anthropic:claude-haiku-4-5-20251001"
```

The `[build]` section is optional. `setup` runs once before the first task (here it initializes a Cargo project in the output directory). `verify` overrides the default verification command for all tasks. See [Configuration](../reference/configuration.md) for all available options.
