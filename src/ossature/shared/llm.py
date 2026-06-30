import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from genai_prices import Usage as GenAIUsage
from genai_prices import calc_price
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)

TRANSPORT_RETRY_ATTEMPTS = 3
TRANSPORT_RETRY_BASE_DELAY = 2.0


def _classify_failure(messages: list[ModelMessage]) -> str:
    """Classify the failure mode from captured messages into a user-friendly sentence."""
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, RetryPromptPart):
                continue
            if isinstance(part.content, list):
                return (
                    "The model produced a response that Ossature couldn't use. "
                    "This typically happens with smaller or local models."
                )
            if part.tool_name:
                return "The model returned an invalid response."
            return (
                "This model doesn't support the response mode Ossature requires. "
                "Not all models are compatible."
            )
    return "The model failed to produce a usable response after multiple attempts."


@dataclass(slots=True)
class LLMRunError(AgentRunError):
    """AgentRunError enriched with operation context for user-friendly reporting."""

    operation: str
    model_name: str | None
    spec_id: str | None
    classification: str
    original: AgentRunError

    def __init__(
        self,
        *,
        operation: str,
        model_name: str | None,
        spec_id: str | None,
        classification: str,
        original: AgentRunError,
    ):
        self.operation = operation
        self.model_name = model_name
        self.spec_id = spec_id
        self.classification = classification
        self.original = original
        super().__init__(original.message)


@dataclass(slots=True)
class UsageTracker:
    """Accumulates token usage and cost across multiple LLM calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0
    model_name: str | None = None

    def add(self, usage: RunUsage, model_name: str | None = None) -> None:
        """Accumulate usage from an agent run result."""
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_tokens or 0
        self.cache_write_tokens += usage.cache_write_tokens or 0
        self.requests += usage.requests or 0
        if model_name and not self.model_name:
            self.model_name = model_name

    def cost(self) -> float | None:
        """Calculate total cost in dollars. Returns None if model is unknown."""
        if not self.model_name:
            return None
        # Parse "provider:model" format
        parts = self.model_name.split(":", 1)
        model_ref = parts[1] if len(parts) == 2 else parts[0]
        provider_id = parts[0] if len(parts) == 2 else None
        try:
            kwargs: dict[str, Any] = {"model_ref": model_ref}
            if provider_id:
                kwargs["provider_id"] = provider_id
            price = calc_price(
                GenAIUsage(
                    input_tokens=self.input_tokens,
                    output_tokens=self.output_tokens,
                    cache_read_tokens=self.cache_read_tokens,
                    cache_write_tokens=self.cache_write_tokens,
                ),
                **kwargs,
            )
            return float(price.total_price)
        except LookupError, ValueError:
            return None

    def format_cost(self) -> str:
        """Format cost as a dollar string like '$0.03', or '?' if unknown."""
        c = self.cost()
        if c is None:
            return "?"
        if c < 0.01:
            return f"${c:.4f}"
        return f"${c:.2f}"

    def format_tokens(self) -> str:
        """Format token counts in a human-readable compact form like '12.3k in, 1.5k out'."""
        return f"{_fmt_tokens(self.input_tokens)} in, {_fmt_tokens(self.output_tokens)} out"

    def format_usage(self) -> str:
        """Full human-readable usage string: tokens + cost."""
        return f"{self.format_tokens()}, {self.format_cost()}"

    def __iadd__(self, other: UsageTracker) -> UsageTracker:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.requests += other.requests
        if other.model_name and not self.model_name:
            self.model_name = other.model_name
        return self


def _fmt_tokens(n: int) -> str:
    """Format token count: 500 -> '500', 1500 -> '1.5k', 1500000 -> '1.5M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def run_agent_sync[OutputT](
    agent: Agent[Any, OutputT],
    prompt: str,
    *,
    operation: str,
    model_name: str | None = None,
    spec_id: str | None = None,
    tracker: UsageTracker | None = None,
    **run_kwargs: Any,
) -> AgentRunResult[OutputT]:
    """Wrap a single agent.run_sync() call with context for better error reporting."""
    for attempt in range(TRANSPORT_RETRY_ATTEMPTS):
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(prompt, **run_kwargs)
                if tracker is not None:
                    tracker.add(result.usage, model_name=model_name)
                return result
            except json.JSONDecodeError:
                if attempt >= TRANSPORT_RETRY_ATTEMPTS - 1:
                    raise
                delay = TRANSPORT_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Malformed API response during %s, retrying in %.0fs (attempt %d/%d)",
                    operation,
                    delay,
                    attempt + 1,
                    TRANSPORT_RETRY_ATTEMPTS,
                )
                time.sleep(delay)
            except AgentRunError as e:
                raise LLMRunError(
                    operation=operation,
                    model_name=model_name,
                    spec_id=spec_id,
                    classification=_classify_failure(messages),
                    original=e,
                ) from e
    raise RuntimeError("Unreachable")  # pragma: no cover
