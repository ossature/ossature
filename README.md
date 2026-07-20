# Ossature

[![CI](https://github.com/ossature/ossature/actions/workflows/ci.yml/badge.svg)](https://github.com/ossature/ossature/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ossature/ossature/branch/master/graph/badge.svg)](https://codecov.io/gh/ossature/ossature)
[![PyPI](https://img.shields.io/pypi/v/ossature)](https://pypi.org/project/ossature/)
[![Downloads](https://img.shields.io/pypi/dm/ossature)](https://pypi.org/project/ossature/)

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/ossature/ossature/blob/master/LICENSE.md)
[![Docs](https://img.shields.io/badge/docs-ossature.dev-blue)](https://docs.ossature.dev)
[![Discord](https://img.shields.io/discord/1480655886589493456?logo=discord&label=Discord)](https://discord.gg/nXqwwpxx73)


> [!WARNING]
> Ossature is currently in its `0.x` series and should be considered **unstable**. APIs, spec formats, CLI flags, and internal behavior may change significantly between releases without prior deprecation. Pin your version and check the [changelog](https://github.com/ossature/ossature/blob/master/CHANGELOG.md) before upgrading.

An open-source build system that turns specs into working code.

You describe what the software should do, review the plan Ossature generates, and build it step by step with an LLM generating the code under tight constraints. Your specs are the source and the generated code is the output, so when requirements change you edit the spec and rebuild only what changed. When a step breaks you fix that step and keep going instead of starting over.

Works with Anthropic, OpenAI, Mistral, Google, and most other hosted providers, as well as local models through Ollama.

*Ossature* (pronounced **OSS-uh-cher**) means the underlying framework or skeleton of a structure.

## What it does

Ossature works from three kinds of files. Only the spec is required.

- **SMD** (spec) states what the software should do: requirements, examples, acceptance criteria.
- **AMD** (architecture) states how it fits together: components, their interfaces, and contracts each component has to hold.
- **VMD** (verification) holds test cases you write yourself, as concrete inputs and expected results.

The workflow is three commands:

- `ossature validate` parses everything and checks it structurally. No LLM involved.
- `ossature audit` has an LLM read each spec, flag problems, and generate a build plan you can read and edit before anything runs.
- `ossature build` runs the plan task by task. Each task generates a few files, runs its verify command, and on failure enters a fix loop. With review on, an LLM also checks the generated code against the spec and the component contracts before a task is done.

Builds are incremental, so changing one spec only rebuilds the tasks it affects, and an upstream change that leaves a spec's public interface unchanged doesn't pull its dependents along. When a spec has a VMD, the plan includes deterministic test tasks built from your cases. The LLM never sees the expected values, so the code can't be written to fit the tests; it has to actually pass them.

## Quick start

Requires Python 3.14+.

```bash
uv tool install ossature
```

Or with pip:

```bash
pip install ossature
```

Set your LLM provider API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# or OPENAI_API_KEY, MISTRAL_API_KEY, etc.
```

Create and build a project:

```bash
ossature init myproject && cd myproject
ossature new my-feature
# edit specs/my-feature.smd
ossature validate
ossature audit
ossature build
```

The default model is `anthropic:claude-sonnet-4-6`. To use a different model, set the `model` field in `ossature.toml`:

```toml
[llm]
model = "openai:gpt-5.2"  # or mistral:devstral-latest, etc.
```

The API key you export must match the provider in your model string, so use `OPENAI_API_KEY` for an `openai:` model. See the [configuration docs](https://docs.ossature.dev/configuration/ossature-toml.html) for per-role overrides and all available options.

## Examples

See [ossature-examples](https://github.com/ossature/ossature-examples) for complete projects with specs, build plans, and generated code.

## Documentation

Full docs at [docs.ossature.dev](https://docs.ossature.dev). The [workflow guide](https://docs.ossature.dev/getting-started/workflow.html) walks through a complete project from init to generated code.

## License

MIT
