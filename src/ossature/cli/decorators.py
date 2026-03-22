import json
import os
import urllib.request
from collections.abc import Callable
from functools import wraps
from typing import Any

import tomli
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded
from rich import box
from rich.console import Console
from rich.panel import Panel

from ossature.config.loader import DEFAULT_OLLAMA_BASE_URL, find_config
from ossature.shared.llm import LLMRunError

# Provider prefix -> (env_var, display_name)
# Providers not listed here either need no key (ollama) or use
# non-standard auth (bedrock uses AWS credentials).
PROVIDER_ENV_VARS: dict[str, tuple[str, str]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "Anthropic"),
    "openai": ("OPENAI_API_KEY", "OpenAI"),
    "google-gla": ("GOOGLE_API_KEY", "Google"),
    "google-vertex": ("GOOGLE_API_KEY", "Google Vertex AI"),
    "gemini": ("GOOGLE_API_KEY", "Google Gemini"),
    "groq": ("GROQ_API_KEY", "Groq"),
    "cohere": ("CO_API_KEY", "Cohere"),
    "openrouter": ("OPENROUTER_API_KEY", "OpenRouter"),
    "xai": ("XAI_API_KEY", "xAI"),
    "mistral": ("MISTRAL_API_KEY", "Mistral"),
    "deepseek": ("DEEPSEEK_API_KEY", "DeepSeek"),
    "fireworks": ("FIREWORKS_API_KEY", "Fireworks AI"),
    "together": ("TOGETHER_API_KEY", "Together AI"),
    "heroku": ("HEROKU_INFERENCE_KEY", "Heroku AI"),
    "github": ("GITHUB_API_KEY", "GitHub Models"),
    "nebius": ("NEBIUS_API_KEY", "Nebius AI"),
    "sambanova": ("SAMBANOVA_API_KEY", "SambaNova"),
    "azure": ("AZURE_OPENAI_API_KEY", "Azure AI"),
    "moonshotai": ("MOONSHOTAI_API_KEY", "MoonshotAI"),
    "ovhcloud": ("OVHCLOUD_API_KEY", "OVHcloud"),
}


def _get_provider_prefix(model_string: str) -> str | None:
    if ":" not in model_string:
        return None
    return model_string.split(":")[0]


def _collect_required_env_vars(config_path: Any) -> dict[str, str]:
    path = config_path
    if path is None:
        path = find_config()
    if path is None or not path.exists():
        return {}

    try:
        data = tomli.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    llm = data.get("llm", {})
    if not llm:
        return {}

    required: dict[str, str] = {}
    for key in ("model", "audit", "build", "planner", "brief", "interface", "fixer"):
        model_str = llm.get(key)
        if not model_str:
            continue
        prefix = _get_provider_prefix(model_str)
        if prefix and prefix in PROVIDER_ENV_VARS:
            env_var, display_name = PROVIDER_ENV_VARS[prefix]
            if env_var not in required:
                required[env_var] = display_name

    return required


def _fetch_ollama_models(base_url: str) -> list[str] | None:
    # Strip /v1 suffix — the native Ollama API lives at the root
    api_root = base_url.removesuffix("/v1").removesuffix("/v1/")
    url = f"{api_root}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return None


def _describe_llm_error(e: AgentRunError) -> tuple[str, str]:
    if isinstance(e, ModelHTTPError):
        status = e.status_code
        model = e.model_name or "unknown"
        if status == 402:
            return (
                f"Insufficient API credits (HTTP {status}, model: {model})",
                "Refill credits and retry.",
            )
        if status == 429:
            return (
                f"Rate limited (HTTP {status}, model: {model})",
                "Wait a moment and retry.",
            )
        if status >= 500:
            return (
                f"API server error (HTTP {status}, model: {model})",
                "The provider may be experiencing issues. Wait and retry.",
            )
        return (
            f"API error (HTTP {status}, model: {model})",
            "Check your API configuration and retry.",
        )
    if isinstance(e, UsageLimitExceeded):
        return (
            "Request limit exceeded",
            "The task exceeded the maximum number of LLM requests.",
        )
    return (e.message, "Check the error and retry.")


def _format_llm_error_body(e: AgentRunError) -> str | None:
    if isinstance(e, ModelHTTPError) and e.body:
        body = e.body
        if isinstance(body, dict):
            msg = (
                body.get("error", {}).get("message")
                if isinstance(body.get("error"), dict)
                else None
            )
            return msg or str(body)
        return str(body)
    return None


def _print_llm_error(console: Console, e: AgentRunError) -> None:
    summary, suggestion = _describe_llm_error(e)
    lines = [summary]
    body = _format_llm_error_body(e)
    if body:
        lines.append(f"\n{body}")
    lines.append(f"\n{suggestion}")
    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold red]LLM Error[/bold red]",
            border_style="red",
            expand=False,
            box=box.ROUNDED,
        )
    )


def _handle_ollama_404(e: ModelHTTPError, console: Console) -> None:
    model_name = e.model_name or "unknown"
    base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
    available = _fetch_ollama_models(base_url)

    console.print(
        f"\n[red]Error:[/] Ollama model [bold]{model_name}[/bold] not found "
        f"on [dim]{base_url}[/dim].\n"
    )

    if available:
        console.print("Available models:\n")
        for name in available:
            console.print(f"  • {name}")
        console.print(
            f"\nPull the model with:  [cyan]ollama pull {model_name}[/cyan]\n"
            f"Or update [bold]model[/bold] in [cyan]ossature.toml[/cyan] to one of the above."
        )
    else:
        console.print(
            f"Could not fetch available models from {base_url}.\n"
            f"Make sure Ollama is running, then pull the model:\n\n"
            f"  [cyan]ollama pull {model_name}[/cyan]"
        )

    console.print()
    raise SystemExit(1)


def _print_contextual_llm_error(console: Console, e: LLMRunError) -> None:
    lines = [f"Failed during {e.operation}"]
    if e.spec_id:
        lines[0] += f" for {e.spec_id}"
    if e.model_name:
        lines.append(f"Model: {e.model_name}")
    lines.append("")
    lines.append(e.classification)
    lines.append("Try a more capable model.")
    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold red]LLM Error[/bold red]",
            border_style="red",
            expand=False,
            box=box.ROUNDED,
        )
    )


def requires_llm(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Extract config_path from the first positional arg or kwargs
        config_path = kwargs.get("config_path") or (args[0] if args else None)

        required = _collect_required_env_vars(config_path)
        missing = {
            env_var: name for env_var, name in required.items() if not os.environ.get(env_var)
        }

        if missing:
            console = kwargs.get("console") or Console()
            lines = []
            for env_var, provider_name in missing.items():
                lines.append(f"  [cyan]export {env_var}=your-key-here[/cyan]  ({provider_name})")
            console.print(
                "[red]Error:[/] Missing API key(s) for configured model provider(s).\n"
                "Set the following environment variable(s):\n\n" + "\n".join(lines) + "\n"
            )
            raise SystemExit(1)

        try:
            return fn(*args, **kwargs)
        except LLMRunError as e:
            console = kwargs.get("console") or Console()
            _print_contextual_llm_error(console, e)
            raise SystemExit(1)
        except ModelHTTPError as e:
            console = kwargs.get("console") or Console()
            if e.status_code == 404 and "OLLAMA_BASE_URL" in os.environ:
                _handle_ollama_404(e, console)
            _print_llm_error(console, e)
            raise SystemExit(1)
        except AgentRunError as e:
            console = kwargs.get("console") or Console()
            _print_llm_error(console, e)
            raise SystemExit(1)

    return wrapper
