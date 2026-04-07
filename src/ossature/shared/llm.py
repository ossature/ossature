import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart
from pydantic_ai.run import AgentRunResult

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


def run_agent_sync[OutputT](
    agent: Agent[Any, OutputT],
    prompt: str,
    *,
    operation: str,
    model_name: str | None = None,
    spec_id: str | None = None,
    **run_kwargs: Any,
) -> AgentRunResult[OutputT]:
    """Wrap a single agent.run_sync() call with context for better error reporting."""
    for attempt in range(TRANSPORT_RETRY_ATTEMPTS):
        with capture_run_messages() as messages:
            try:
                return agent.run_sync(prompt, **run_kwargs)
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
