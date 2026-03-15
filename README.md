# Ossature

[![CI](https://github.com/ossature/ossature/actions/workflows/ci.yml/badge.svg)](https://github.com/ossature/ossature/actions/workflows/ci.yml)

An open-source harness for spec-driven code generation.

You write a specification, optionally lay out the architecture, and Ossature breaks it down into a build plan that gets executed step by step with an LLM doing the code generation under tight constraints. The specs are your source of truth, you review the plan before anything gets built, and when something breaks you fix that step and keep going instead of starting over.

Works with Anthropic, OpenAI, Mistral, Google, and most other hosted providers, as well as local models through Ollama.

*Ossature* (pronounced **OSS-uh-cher**) means the underlying framework or skeleton of a structure.

## Quick start

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ossature/ossature.git
cd ossature
uv sync
uv run ossature --version
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

## Examples

See [ossature-examples](https://github.com/ossature/ossature-examples) for complete projects with specs, build plans, and generated code.

## Documentation

Full docs at [docs.ossature.dev](https://docs.ossature.dev). The [workflow guide](https://docs.ossature.dev/getting-started/workflow.html) walks through a complete project from init to generated code.

## License

MIT
