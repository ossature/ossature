"""Build agent construction and LLM call retry handling."""

import json
import time
from typing import Any

from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, RetryPromptPart
from pydantic_ai.usage import UsageLimits
from rich.console import Console

from ossature.build.tools import BuildContext, _register_tools
from ossature.config.loader import OssatureConfig
from ossature.models.review import ReviewReport
from ossature.promptspec import render
from ossature.shared.llm import UsageTracker


def _create_impl_agent(config: OssatureConfig) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        config.llm.model_for("build"),
        system_prompt=render("build.implementer", language=config.output.language),
        deps_type=BuildContext,
        retries={"tools": config.llm.tool_retries},
        model_settings={"max_tokens": config.build.max_output_tokens},
    )
    _register_tools(agent)
    return agent


def _create_fix_agent(config: OssatureConfig) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        config.llm.model_for("build"),
        system_prompt=render("build.fixer", language=config.output.language),
        deps_type=BuildContext,
        retries={"tools": config.llm.tool_retries},
        model_settings={"max_tokens": config.build.max_output_tokens},
    )
    _register_tools(agent)
    return agent


def _create_review_agent(config: OssatureConfig) -> Agent[None, ReviewReport]:
    return Agent(
        config.llm.model_for("reviewer"),
        output_type=ReviewReport,
        system_prompt=render("build.reviewer", language=config.output.language),
        retries={"output": config.llm.retries},
    )


_STRUCTURAL_ERROR_PATTERNS: tuple[str, ...] = (
    "missing key",
    "is not an object",
    "Expected a JSON array",
    "Could not parse edits JSON",
    "must both be strings",
    "Field required",
    "validation error",
)

_EDIT_SCHEMA_REMINDER: str = (
    "\n\n<important>\n"
    "IMPORTANT: When using `edit_file`, the `edits` parameter must be a list of objects "
    'with exactly two keys: "old" and "new". Example:\n'
    'edit_file(path="src/main.py", edits=[{"old": "text to find", "new": "replacement"}])\n'
    "Do NOT use key names like 'old_str', 'new_str', 'search', 'replace', or any variant.\n"
    "</important>"
)


def _extract_last_retry_error(messages: list[Any]) -> str | None:
    """Walk captured messages backwards to find the last tool-retry error content."""
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, RetryPromptPart) and isinstance(part.content, str):
                return part.content
    return None


def _is_structural_tool_error(detail: str | None) -> bool:
    """Check if a retry error indicates structural schema confusion (not content errors)."""
    if not detail:
        return False
    detail_lower = detail.lower()
    return any(p.lower() in detail_lower for p in _STRUCTURAL_ERROR_PATTERNS)


def _run_with_retry(
    agent: Agent[BuildContext, str],
    prompt: str,
    deps: BuildContext,
    console: Console,
    max_retries: int = 5,
    base_delay: float = 30.0,
    tracker: UsageTracker | None = None,
    model_name: str | None = None,
) -> Any:
    _structural_retried = False
    for attempt in range(max_retries):
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    prompt, deps=deps, usage_limits=UsageLimits(request_limit=200)
                )
                if tracker is not None:
                    tracker.add(result.usage, model_name=model_name)
                return result
            except json.JSONDecodeError:
                if attempt >= max_retries - 1:
                    raise
                delay = base_delay * (2**attempt)
                console.log(
                    f"    [yellow]Malformed API response, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})[/yellow]"
                )
                time.sleep(delay)
            except ModelHTTPError as e:
                if e.status_code != 429 or attempt >= max_retries - 1:
                    raise
                delay = base_delay * (2**attempt)
                console.log(
                    f"    [yellow] Rate limited, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})[/yellow]"
                )
                time.sleep(delay)
            except AgentRunError as e:
                detail = _extract_last_retry_error(messages)
                if _is_structural_tool_error(detail) and not _structural_retried:
                    _structural_retried = True
                    console.log(
                        "    [yellow]Structural tool-call error — "
                        "retrying with fresh context[/yellow]"
                    )
                    prompt = prompt + _EDIT_SCHEMA_REMINDER
                    continue
                if detail:
                    e._last_retry_detail = detail  # type: ignore[attr-defined]
                raise
    raise RuntimeError("Unreachable")


def _describe_llm_error(e: AgentRunError) -> tuple[str, str]:
    if isinstance(e, ModelHTTPError):
        status = e.status_code
        if status == 402:
            return (
                f"Insufficient API credits (HTTP {status})",
                "Refill credits and retry.",
            )
        if status == 429:
            return (
                f"Rate limited (HTTP {status})",
                "Rate limit retries exhausted. Wait and retry.",
            )
        if status >= 500:
            return (
                f"API server error (HTTP {status})",
                "The provider may be experiencing issues. Wait and retry.",
            )
        return (
            f"API error (HTTP {status})",
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
