# Configuration

Everything starts with `ossature.toml` at the project root.

## Minimal Config

```toml
[project]
name = "myproject"
version = "0.1.0"
spec_dir = "specs"

[output]
dir = "output"
language = "python"

[llm]
model = "anthropic:claude-sonnet-4-6"
```

The `[llm]` section with a `model` field is required. Everything else has defaults.

## Project Section

```toml
[project]
name = "myproject"
version = "0.1.0"
spec_dir = "specs"       # where .smd and .amd files live
context_dir = "context"  # where user-provided context files live
```

Ossature discovers spec files automatically by scanning `spec_dir` recursively. You don't list them in the config.

## Output Section

```toml
[output]
dir = "output"           # where generated code goes
language = "python"      # target language
```

The `language` field tells the LLM what language to generate. It's not limited to a fixed list, but you'll get best results with common languages like python, typescript, rust, go, lua, etc.

## Audit Section

```toml
[audit]
max_fix_cycles = 3       # audit → fix → re-audit cycles per spec
```

Controls how many times the audit will attempt to fix errors in a spec and re-audit it. Each cycle sends the findings to the fixer LLM, applies edits, then re-audits the changed file. Defaults to 3.

## Build Section

```toml
[build]
max_fix_attempts = 3                  # verify-fail -> fix -> re-verify cycles per task
max_inline_lines = 200                # files above this aren't inlined in fix prompts
max_output_tokens = 32768             # per-call output token limit for the build LLM
setup = ["cargo init"]                # optional: run before the first task
verify = ["cargo check"]              # optional: override default verification
test = ["cargo test"]                 # optional: override default test command
```

The `setup`, `verify`, and `test` fields are lists of shell commands. Each list item runs in its own shell, in order, and the build stops as soon as any command exits non-zero. A bare string is also accepted for backwards compatibility, so `verify = "cargo check"` keeps working and is treated the same as `verify = ["cargo check"]`. New configs should use lists. They're easier to read for multi-step pipelines and the pre-flight tool check can look at each step on its own.

```toml
[build]
verify = [
    "gcc -Wall -o /tmp/build_check sample.c",
    "/tmp/build_check --help > /dev/null",
    "/tmp/build_check --version > /dev/null",
]
```

The `setup` commands run once before the first build task. Useful for project initialization that the LLM shouldn't handle.

The `verify` and `test` commands override what Ossature uses to check generated code. If not set, the LLM determines verification commands per task based on the language and project structure.

The `max_inline_lines` field controls when files are inlined in fix prompts. When a task fails verification, Ossature sends the fixer LLM the error output and the current file contents. Files with more lines than this threshold are not inlined; the fixer uses its `read_lines` and `grep_file` tools to inspect them instead. This prevents blowing up the prompt on large files. Defaults to 200.

The `max_output_tokens` field caps how much output the implementer and fixer agents can produce in a single LLM call. A single `write_file` tool call counts toward this budget, so tasks that produce very large source files (a few thousand lines) can hit the cap. Defaults to 32768, which fits most files comfortably. If you see "Model token limit exceeded before any response was generated", bump this higher. Sonnet-class models can go up to 64000 or so.

## LLM Section

```toml
[llm]
model = "anthropic:claude-sonnet-4-6"
```

The model format is `provider:model-name`. Any provider supported by pydantic_ai works, including `anthropic`, `openai`, `google-gla`, `groq`, `mistral`, `ollama`, and others.

When the config is loaded, Ossature checks each model string against pydantic_ai offline. A typo in the provider (e.g. `anthrop:claude-sonnet-4-6`) or an unrecognized model name (e.g. `anthropic:claude-sonnt-4-6`) prints a warning with close-match suggestions but does not block the run, so newly released models still work even if pydantic_ai's known list is behind. If the provider rejects the model at request time, the resulting error panel points back at the `[llm]` section.

The `retries` field controls how many times an agent retries when the model returns an invalid structured response (e.g., malformed JSON output). Defaults to 3.

The `tool_retries` field controls how many times a tool call can be retried when the LLM makes a mistake (e.g., `edit_file` with text that doesn't match the file). Defaults to 5. Increase for less capable or local models.

```toml
[llm]
model = "anthropic:claude-sonnet-4-6"
retries = 5
tool_retries = 8
```

Set your API key as an environment variable:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Per-Role Overrides

You can use different models for different stages. This lets you use a stronger model for auditing and a faster one for fixing compilation errors.

```toml
[llm]
model = "anthropic:claude-sonnet-4-6"     # default for all roles
audit = "anthropic:claude-opus-4-6"       # spec review
planner = "anthropic:claude-sonnet-4-6"   # plan generation
build = "anthropic:claude-sonnet-4-6"     # code generation
fixer = "anthropic:claude-sonnet-4-6"     # fixing failed tasks
brief = "anthropic:claude-sonnet-4-6"     # brief generation
interface = "anthropic:claude-sonnet-4-6" # interface extraction
```

Any role that isn't explicitly set falls back to the default `model`.

### Ollama (Local Models)

```toml
[llm]
model = "ollama:devstral-latest"
ollama_base_url = "http://localhost:11434/v1"   # optional, this is the default
```

You can mix providers. Use Ollama for code generation and Anthropic for auditing:

```toml
[llm]
model = "ollama:devstral-latest"
audit = "anthropic:claude-opus-4-6"
planner = "anthropic:claude-sonnet-4-6"
fixer = "anthropic:claude-sonnet-4-6"
```

## Config Discovery

Ossature searches for `ossature.toml` by walking up from the current directory. Override with `--config`:

```bash
ossature build --config /path/to/ossature.toml
```

## Full Example

Here's a config for a Lua game project using context files:

```toml
[project]
name = "math_quest"
version = "0.1.0"
spec_dir = "specs"
context_dir = "context"

[output]
dir = "output"
language = "lua"

[llm]
model = "anthropic:claude-opus-4-6"
```

And one for a Python CLI tool with mixed models:

```toml
[project]
name = "Spenny"
version = "0.1.0"
spec_dir = "specs"

[output]
dir = "output"
language = "python"

[llm]
model = "ollama:devstral-latest"
audit = "anthropic:claude-opus-4-6"
planner = "anthropic:claude-sonnet-4-6"
fixer = "anthropic:claude-sonnet-4-6"
```
