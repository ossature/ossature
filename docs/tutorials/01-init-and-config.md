# 1. Initialize the Project

**What you'll do:** Create the project directory and write the initial configuration. By the end of this step you'll have an `ossature.toml` and an empty `specs/` directory ready for your spec files.

This is the first step of the markman tutorial. [Next: Write your specs](02-writing-specs.md).

## Initialize

```bash
ossature init markman
cd markman
```

You should see two new items in the directory: `ossature.toml` and a `specs/` folder.

!!! tip "If something goes wrong"
    If `ossature init` fails with a Python version error, check that you have Python 3.14 or higher installed (`python --version`).

## Configure

Open `ossature.toml` and set it up for the markman project:

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

The `[build]` section is optional. `setup` runs once before the first task; here it initializes a Cargo project in the output directory. `verify` sets the default verification command for tasks that do not specify their own. See [Configuration](../reference/configuration.md) for all available options.

Export your API key before proceeding:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Next step

[Write your specs](02-writing-specs.md) -- Create the SMD files that describe each module of the markman project.
