# Installation

## Requirements

- Python 3.14 or higher
- [uv](https://docs.astral.sh/uv/) (for package management)
- An LLM provider API key (Anthropic, OpenAI, Mistral, OpenRouter, etc. or local Ollama)

## Install from Source

```bash
git clone https://github.com/ossature/ossature.git
cd ossature
uv sync
```

## Configure Your API Key

Set the environment variable for your LLM provider:

```bash
# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

# OpenAI
export OPENAI_API_KEY="sk-..."
```

If you're using Ollama, no API key is needed. Just make sure the Ollama server is running:

```bash
ollama serve
```

## Verify Installation

```bash
ossature --version
```

## Next Steps

- [Quick Start](quickstart.md) - Create your first project
- [Workflow Guide](workflow.md) - Full walkthrough from init to build
