import json
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_task
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded

from ossature.build.builder import (
    _describe_llm_error,
    _format_llm_error_body,
    _is_structural_tool_error,
    _print_llm_error,
    _run_with_retry,
)


class TestIsStructuralToolError:
    def test_none_detail(self):
        assert _is_structural_tool_error(None) is False

    def test_empty_string(self):
        assert _is_structural_tool_error("") is False

    def test_missing_key_old(self):
        assert _is_structural_tool_error("Edit #1 is missing key(s): old, new") is True

    def test_missing_key_new(self):
        assert _is_structural_tool_error("Edit #1 is missing key(s): new") is True

    def test_not_an_object(self):
        assert _is_structural_tool_error("Edit #1 is not an object (got str)") is True

    def test_expected_json_array(self):
        assert _is_structural_tool_error("Expected a JSON array of edits") is True

    def test_could_not_parse(self):
        assert _is_structural_tool_error("Could not parse edits JSON: ...") is True

    def test_must_both_be_strings(self):
        assert _is_structural_tool_error('"old" and "new" must both be strings.') is True

    def test_pydantic_field_required(self):
        assert _is_structural_tool_error("Field required [type=missing]") is True

    def test_pydantic_validation_error(self):
        assert _is_structural_tool_error("2 validation errors for FileEdit") is True

    def test_content_error_not_structural(self):
        assert _is_structural_tool_error("the `old` text was not found in the file") is False

    def test_ambiguous_match_not_structural(self):
        assert _is_structural_tool_error("the `old` text matches 3 locations") is False

    def test_case_insensitive(self):
        assert _is_structural_tool_error("MISSING KEY(s): old") is True


class TestDescribeLlmError:
    def test_402_insufficient_credits(self):
        e = ModelHTTPError(status_code=402, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "402" in summary
        assert "credits" in summary.lower()
        assert "retry" in suggestion.lower()

    def test_429_rate_limited(self):
        e = ModelHTTPError(status_code=429, model_name="claude")
        summary, _suggestion = _describe_llm_error(e)
        assert "429" in summary
        assert "rate" in summary.lower()

    def test_500_server_error(self):
        e = ModelHTTPError(status_code=500, model_name="claude")
        summary, _suggestion = _describe_llm_error(e)
        assert "500" in summary
        assert "server" in summary.lower()

    def test_502_server_error(self):
        e = ModelHTTPError(status_code=502, model_name="claude")
        summary, _ = _describe_llm_error(e)
        assert "502" in summary
        assert "server" in summary.lower()

    def test_503_server_error(self):
        e = ModelHTTPError(status_code=503, model_name="claude")
        summary, _ = _describe_llm_error(e)
        assert "503" in summary

    def test_other_4xx(self):
        e = ModelHTTPError(status_code=400, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "400" in summary
        assert "configuration" in suggestion.lower()

    def test_usage_limit_exceeded(self):
        e = UsageLimitExceeded("too many requests")
        summary, _ = _describe_llm_error(e)
        assert "limit" in summary.lower()

    def test_generic_agent_run_error(self):
        e = AgentRunError("something went wrong")
        summary, suggestion = _describe_llm_error(e)
        assert "something went wrong" in summary
        assert "retry" in suggestion.lower()


class TestFormatLlmErrorBody:
    def test_extracts_error_message_from_dict(self):
        e = ModelHTTPError(
            status_code=400,
            model_name="claude",
            body={
                "error": {
                    "message": "max tokens must be less than 8192",
                    "type": "invalid_request_error",
                }
            },
        )
        assert _format_llm_error_body(e) == "max tokens must be less than 8192"

    def test_falls_back_to_str_for_flat_dict(self):
        e = ModelHTTPError(status_code=400, model_name="claude", body={"detail": "bad request"})
        result = _format_llm_error_body(e)
        assert "bad request" in result

    def test_returns_str_body_directly(self):
        e = ModelHTTPError(status_code=500, model_name="claude", body="Internal Server Error")
        assert _format_llm_error_body(e) == "Internal Server Error"

    def test_returns_none_when_no_body(self):
        e = ModelHTTPError(status_code=429, model_name="claude")
        assert _format_llm_error_body(e) is None

    def test_returns_none_for_non_http_error(self):
        e = AgentRunError("something")
        assert _format_llm_error_body(e) is None


class TestPrintLlmError:
    def test_prints_panel_with_error_info(self):
        console = MagicMock()
        task = make_task("003", "AUTH")
        e = ModelHTTPError(status_code=402, model_name="claude")

        _print_llm_error(console, task, 34, e)

        console.log.assert_called_once()
        log_args = console.log.call_args[0][0]
        assert "003" in log_args
        assert "AUTH task 003" in log_args

        console.print.assert_called()


class TestRunWithRetryJsonDecode:
    @patch("ossature.build.builder.time.sleep")
    def test_retries_on_json_decode_error(self, mock_sleep):
        mock_result = MagicMock()
        agent = MagicMock()
        agent.run_sync.side_effect = [
            json.JSONDecodeError("Expecting value", "", 0),
            mock_result,
        ]
        console = MagicMock()
        deps = MagicMock()

        result = _run_with_retry(agent, "prompt", deps, console, max_retries=3, base_delay=1.0)

        assert result is mock_result
        assert agent.run_sync.call_count == 2
        mock_sleep.assert_called_once()
        console.log.assert_called_once()

    @patch("ossature.build.builder.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        agent = MagicMock()
        agent.run_sync.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        console = MagicMock()
        deps = MagicMock()

        with pytest.raises(json.JSONDecodeError):
            _run_with_retry(agent, "prompt", deps, console, max_retries=3, base_delay=1.0)

        assert agent.run_sync.call_count == 3
