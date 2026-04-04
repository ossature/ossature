from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded

from ossature.cli.decorators import (
    _collect_required_env_vars,
    _describe_llm_error,
    _format_llm_error_body,
    _get_provider_prefix,
)


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
