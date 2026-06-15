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

An open-source build system that turns specs and architecture into working code.

You write a spec for what the software does, optionally lay out the architecture for how it fits together, and Ossature turns that into a build plan that runs step by step, with an LLM generating the code under tight constraints. Your specs and architecture are the source, the generated code is the output. You review the plan before anything runs, and when something breaks you fix that step and keep going instead of starting over.

Works with Anthropic, OpenAI, Mistral, Google, and most other hosted providers, as well as local models through Ollama.

*Ossature* (pronounced **OSS-uh-cher**) means the underlying framework or skeleton of a structure.

## Quick start

Requires Python 3.14+.

```bash
pip install ossature
```

Or run it directly with [uvx](https://docs.astral.sh/uv/):

```bash
uvx ossature --version
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

The API key you export must match the provider in your model string (e.g., `OPENAI_API_KEY` for `openai:…`). See the [configuration docs](https://docs.ossature.dev/configuration/ossature-toml.html) for per-role overrides and all available options.

## Examples

See [ossature-examples](https://github.com/ossature/ossature-examples) for complete projects with specs, build plans, and generated code.

## Documentation

Full docs at [docs.ossature.dev](https://docs.ossature.dev). The [workflow guide](https://docs.ossature.dev/getting-started/workflow.html) walks through a complete project from init to generated code.

## License

MIT
