import json

import pytest
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded, UserError
from rich.console import Console

from ossature.cli.decorators import (
    _collect_required_env_vars,
    _describe_llm_error,
    _format_llm_error_body,
    _get_provider_prefix,
    _print_contextual_llm_error,
    requires_llm,
)
from ossature.shared.llm import LLMRunError


class TestGetProviderPrefix:
    def test_anthropic(self):
        assert _get_provider_prefix("anthropic:claude-sonnet-4-6") == "anthropic"

    def test_ollama(self):
        assert _get_provider_prefix("ollama:deepseek-coder") == "ollama"

    def test_no_colon(self):
        assert _get_provider_prefix("plain-model") is None

    def test_multiple_colons(self):
        assert _get_provider_prefix("openai:gpt:4") == "openai"

    def test_empty_string(self):
        assert _get_provider_prefix("") is None


class TestCollectRequiredEnvVars:
    def test_single_model(self, temp_dir):
        config = temp_dir / "ossature.toml"
        config.write_text('[llm]\nmodel = "anthropic:claude-sonnet-4-6"\n')
        result = _collect_required_env_vars(config)
        assert result == {"ANTHROPIC_API_KEY": "Anthropic"}

    def test_multiple_models(self, temp_dir):
        config = temp_dir / "ossature.toml"
        config.write_text('[llm]\nmodel = "anthropic:claude-sonnet-4-6"\naudit = "openai:gpt-4o"\n')
        result = _collect_required_env_vars(config)
        assert result == {"ANTHROPIC_API_KEY": "Anthropic", "OPENAI_API_KEY": "OpenAI"}

    def test_ollama_model_no_env_var(self, temp_dir):
        config = temp_dir / "ossature.toml"
        config.write_text('[llm]\nmodel = "ollama:deepseek-coder"\n')
        result = _collect_required_env_vars(config)
        assert result == {}

    def test_missing_config(self, temp_dir):
        result = _collect_required_env_vars(temp_dir / "nonexistent.toml")
        assert result == {}

    def test_no_llm_section(self, temp_dir):
        config = temp_dir / "ossature.toml"
        config.write_text('[project]\nname = "test"\n')
        result = _collect_required_env_vars(config)
        assert result == {}


class TestDescribeLlmError:
    def test_402_credits(self):
        e = ModelHTTPError(status_code=402, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "credits" in summary.lower()
        assert "retry" in suggestion.lower()

    def test_429_rate_limit(self):
        e = ModelHTTPError(status_code=429, model_name="claude")
        summary, _suggestion = _describe_llm_error(e)
        assert "rate" in summary.lower()

    def test_500_server(self):
        e = ModelHTTPError(status_code=500, model_name="claude")
        summary, _suggestion = _describe_llm_error(e)
        assert "server" in summary.lower()

    def test_generic_4xx(self):
        e = ModelHTTPError(status_code=400, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "400" in summary
        assert "configuration" in suggestion.lower()

    def test_usage_limit(self):
        e = UsageLimitExceeded("too many")
        summary, _ = _describe_llm_error(e)
        assert "limit" in summary.lower()

    def test_generic_error(self):
        e = AgentRunError("something broke")
        summary, suggestion = _describe_llm_error(e)
        assert "something broke" in summary
        assert "retry" in suggestion.lower()


class TestFormatLlmErrorBody:
    def test_dict_with_error_message(self):
        e = ModelHTTPError(
            status_code=400,
            model_name="claude",
            body={"error": {"message": "details"}},
        )
        assert _format_llm_error_body(e) == "details"

    def test_string_body(self):
        e = ModelHTTPError(status_code=500, model_name="claude", body="error text")
        assert _format_llm_error_body(e) == "error text"

    def test_no_body(self):
        e = ModelHTTPError(status_code=429, model_name="claude")
        assert _format_llm_error_body(e) is None

    def test_non_http_error(self):
        e = AgentRunError("something")
        assert _format_llm_error_body(e) is None


class TestRequiresLlmJsonDecodeError:
    def test_catches_json_decode_error(self, tmp_path):
        config_path = tmp_path / "ossature.toml"
        config_path.write_text('[llm]\nmodel = "ollama:test"\n')

        @requires_llm
        def failing_fn(config_path, **kwargs):
            raise json.JSONDecodeError("Expecting value", "", 0)

        with pytest.raises(SystemExit, match="1"):
            failing_fn(config_path)


class TestRequiresLlmUserError:
    def test_unknown_model_renders_friendly_panel(self, tmp_path, capsys):
        config_path = tmp_path / "ossature.toml"
        config_path.write_text('[llm]\nmodel = "ollama:test"\n')
        console = Console(force_terminal=False, width=120)

        @requires_llm
        def failing_fn(config_path, **kwargs):
            raise UserError("Unknown model: openai_gpt-5.5")

        with pytest.raises(SystemExit, match="1"):
            failing_fn(config_path, console=console)

        out = capsys.readouterr().out
        assert "Configuration Error" in out
        assert "Unknown model: openai_gpt-5.5" in out
        assert "provider:model" in out

    def test_other_user_error_renders_panel_without_format_hint(self, tmp_path, capsys):
        config_path = tmp_path / "ossature.toml"
        config_path.write_text('[llm]\nmodel = "ollama:test"\n')
        console = Console(force_terminal=False, width=120)

        @requires_llm
        def failing_fn(config_path, **kwargs):
            raise UserError("Some other configuration problem")

        with pytest.raises(SystemExit, match="1"):
            failing_fn(config_path, console=console)

        out = capsys.readouterr().out
        assert "Configuration Error" in out
        assert "Some other configuration problem" in out
        assert "provider:model" not in out


class TestDescribeLlm404:
    def test_404_describes_model_not_found(self):
        e = ModelHTTPError(status_code=404, model_name="xyz")
        summary, suggestion = _describe_llm_error(e)
        assert "404" in summary
        assert "xyz" in summary
        assert "ossature.toml" in suggestion


class TestContextualLLMErrorRouting:
    """LLMRunError unwraps to the structured _print_llm_error path when the
    underlying failure carries provider-specific details."""

    def _wrap(self, original: AgentRunError) -> LLMRunError:
        return LLMRunError(
            operation="spec audit",
            model_name="openai:xyz",
            spec_id="FOO",
            classification="The model failed to produce a usable response",
            original=original,
        )

    def test_404_uses_structured_path_with_context(self, capsys):
        console = Console(force_terminal=False, width=120)
        _print_contextual_llm_error(
            console, self._wrap(ModelHTTPError(status_code=404, model_name="xyz"))
        )
        out = capsys.readouterr().out
        # Context preserved
        assert "Failed during spec audit for FOO" in out
        # Structured 404 message used (not the heuristic classification)
        assert "404" in out
        assert "ossature.toml" in out
        assert "Try a more capable model" not in out

    def test_429_uses_structured_path(self, capsys):
        console = Console(force_terminal=False, width=120)
        _print_contextual_llm_error(
            console,
            self._wrap(
                ModelHTTPError(
                    status_code=429,
                    model_name="claude",
                    body={"error": {"message": "rate limited"}},
                )
            ),
        )
        out = capsys.readouterr().out
        assert "Rate limited" in out
        assert "rate limited" in out  # body surfaced
        assert "Try a more capable model" not in out

    def test_non_api_error_keeps_classification(self, capsys):
        console = Console(force_terminal=False, width=120)
        _print_contextual_llm_error(console, self._wrap(AgentRunError("transport boom")))
        out = capsys.readouterr().out
        # No structured info -> heuristic classification path
        assert "The model failed to produce a usable response" in out
        assert "Try a more capable model" in out
