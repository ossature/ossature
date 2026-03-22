from unittest.mock import MagicMock

from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart

from ossature.shared.llm import LLMRunError, _classify_failure, run_agent_sync


class TestClassifyFailure:
    def test_plain_text_not_permitted(self):
        messages = [
            ModelRequest.user_text_prompt("test"),
            ModelResponse(parts=[TextPart(content="hello")]),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content=(
                            "Plain text responses are not permitted, "
                            "please call one of the provided tools"
                        )
                    )
                ]
            ),
        ]
        result = _classify_failure(messages)
        assert "response mode" in result.lower()

    def test_validation_errors_list(self):
        error_details = [
            {"type": "missing", "loc": ("tasks", 0, "title"), "msg": "Field required"},
        ]
        messages = [
            ModelRequest(parts=[RetryPromptPart(content=error_details)]),
        ]
        result = _classify_failure(messages)
        assert "couldn't use" in result.lower()

    def test_tool_retry_string(self):
        messages = [
            ModelRequest(
                parts=[RetryPromptPart(content="Something went wrong", tool_name="my_tool")]
            ),
        ]
        result = _classify_failure(messages)
        assert "invalid response" in result.lower()

    def test_generic_string_no_tool(self):
        messages = [
            ModelRequest(parts=[RetryPromptPart(content="Something unexpected happened")]),
        ]
        result = _classify_failure(messages)
        assert "response mode" in result.lower()

    def test_empty_messages(self):
        result = _classify_failure([])
        assert "failed to produce" in result.lower()

    def test_no_retry_parts(self):
        messages = [
            ModelRequest.user_text_prompt("test"),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        result = _classify_failure(messages)
        assert "failed to produce" in result.lower()

    def test_uses_last_retry_part(self):
        messages = [
            ModelRequest(
                parts=[RetryPromptPart(content="Something went wrong", tool_name="my_tool")]
            ),
            ModelResponse(parts=[TextPart(content="still wrong")]),
            ModelRequest(parts=[RetryPromptPart(content="Plain text responses are not permitted")]),
        ]
        result = _classify_failure(messages)
        assert "response mode" in result.lower()


class TestLLMRunError:
    def test_is_agent_run_error_subclass(self):
        original = AgentRunError("Exceeded maximum retries")
        e = LLMRunError(
            operation="spec audit",
            model_name="ollama:llama3",
            spec_id="AUTH",
            classification="This model doesn't support structured output.",
            original=original,
        )
        assert isinstance(e, AgentRunError)

    def test_preserves_original_message(self):
        original = AgentRunError("Exceeded maximum retries")
        e = LLMRunError(
            operation="spec audit",
            model_name="ollama:llama3",
            spec_id="AUTH",
            classification="This model doesn't support structured output.",
            original=original,
        )
        assert e.message == "Exceeded maximum retries"

    def test_stores_context(self):
        original = AgentRunError("error")
        e = LLMRunError(
            operation="plan generation",
            model_name="openai:gpt-4",
            spec_id="DATABASE",
            classification="This model returned malformed output.",
            original=original,
        )
        assert e.operation == "plan generation"
        assert e.model_name == "openai:gpt-4"
        assert e.spec_id == "DATABASE"
        assert e.classification == "This model returned malformed output."
        assert e.original is original

    def test_no_spec_id(self):
        original = AgentRunError("error")
        e = LLMRunError(
            operation="cross-spec audit",
            model_name=None,
            spec_id=None,
            classification="The model failed to produce a valid response.",
            original=original,
        )
        assert e.spec_id is None
        assert e.model_name is None


class TestRunAgentSync:
    def test_returns_result_on_success(self):
        mock_result = MagicMock()
        mock_result.output = "plan data"
        agent = MagicMock()
        agent.run_sync.return_value = mock_result

        result = run_agent_sync(agent, "test prompt", operation="test")

        assert result is mock_result
        agent.run_sync.assert_called_once_with("test prompt")

    def test_passes_kwargs_to_run_sync(self):
        mock_result = MagicMock()
        agent = MagicMock()
        agent.run_sync.return_value = mock_result

        deps = MagicMock()
        run_agent_sync(agent, "test prompt", operation="test", deps=deps)

        agent.run_sync.assert_called_once_with("test prompt", deps=deps)

    def test_wraps_agent_run_error_with_context(self):
        original = AgentRunError("Exceeded maximum retries")
        agent = MagicMock()
        agent.run_sync.side_effect = original

        try:
            run_agent_sync(
                agent,
                "test prompt",
                operation="spec audit",
                model_name="anthropic:claude-sonnet-4-6",
                spec_id="AUTH",
            )
            assert False, "Should have raised"
        except LLMRunError as e:
            assert e.operation == "spec audit"
            assert e.spec_id == "AUTH"
            assert e.model_name == "anthropic:claude-sonnet-4-6"
            assert e.original is original
            assert isinstance(e, AgentRunError)

    def test_no_spec_id_when_omitted(self):
        agent = MagicMock()
        agent.run_sync.side_effect = AgentRunError("error")

        try:
            run_agent_sync(agent, "prompt", operation="cross-spec audit", model_name="test:model")
            assert False, "Should have raised"
        except LLMRunError as e:
            assert e.spec_id is None
            assert e.operation == "cross-spec audit"
            assert e.model_name == "test:model"

    def test_model_name_none_when_omitted(self):
        agent = MagicMock()
        agent.run_sync.side_effect = AgentRunError("error")

        try:
            run_agent_sync(agent, "prompt", operation="test")
            assert False, "Should have raised"
        except LLMRunError as e:
            assert e.model_name is None
