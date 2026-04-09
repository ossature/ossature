from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.usage import RunUsage

from ossature.build.builder import TaskResult
from ossature.shared.llm import LLMRunError, UsageTracker, _fmt_tokens, run_agent_sync


class TestFmtTokens:
    def test_small_number(self):
        assert _fmt_tokens(0) == "0"
        assert _fmt_tokens(500) == "500"
        assert _fmt_tokens(999) == "999"

    def test_thousands(self):
        assert _fmt_tokens(1_000) == "1.0k"
        assert _fmt_tokens(1_500) == "1.5k"
        assert _fmt_tokens(12_345) == "12.3k"
        assert _fmt_tokens(999_999) == "1000.0k"

    def test_millions(self):
        assert _fmt_tokens(1_000_000) == "1.0M"
        assert _fmt_tokens(1_500_000) == "1.5M"
        assert _fmt_tokens(12_345_678) == "12.3M"


class TestUsageTrackerAdd:
    def test_accumulates_tokens(self):
        tracker = UsageTracker()
        usage = RunUsage(input_tokens=100, output_tokens=50, requests=1)
        tracker.add(usage)

        assert tracker.input_tokens == 100
        assert tracker.output_tokens == 50
        assert tracker.requests == 1

    def test_accumulates_multiple_calls(self):
        tracker = UsageTracker()
        tracker.add(RunUsage(input_tokens=100, output_tokens=50, requests=1))
        tracker.add(RunUsage(input_tokens=200, output_tokens=75, requests=1))

        assert tracker.input_tokens == 300
        assert tracker.output_tokens == 125
        assert tracker.requests == 2

    def test_accumulates_cache_tokens(self):
        tracker = UsageTracker()
        tracker.add(
            RunUsage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=30,
                cache_write_tokens=20,
                requests=1,
            )
        )

        assert tracker.cache_read_tokens == 30
        assert tracker.cache_write_tokens == 20

    def test_sets_model_name_from_first_call(self):
        tracker = UsageTracker()
        tracker.add(RunUsage(requests=1), model_name="anthropic:claude-sonnet-4-6")
        tracker.add(RunUsage(requests=1), model_name="openai:gpt-4o")

        assert tracker.model_name == "anthropic:claude-sonnet-4-6"


class TestUsageTrackerIadd:
    def test_combines_two_trackers(self):
        a = UsageTracker(input_tokens=100, output_tokens=50, requests=1)
        b = UsageTracker(input_tokens=200, output_tokens=75, requests=2)
        a += b

        assert a.input_tokens == 300
        assert a.output_tokens == 125
        assert a.requests == 3

    def test_preserves_model_name(self):
        a = UsageTracker(model_name="anthropic:claude-sonnet-4-6")
        b = UsageTracker(model_name="openai:gpt-4o")
        a += b

        assert a.model_name == "anthropic:claude-sonnet-4-6"

    def test_inherits_model_name_if_none(self):
        a = UsageTracker()
        b = UsageTracker(model_name="openai:gpt-4o")
        a += b

        assert a.model_name == "openai:gpt-4o"


class TestUsageTrackerCost:
    def test_returns_none_without_model(self):
        tracker = UsageTracker(input_tokens=1000, output_tokens=500)
        assert tracker.cost() is None

    def test_returns_none_for_unknown_model(self):
        tracker = UsageTracker(
            input_tokens=1000, output_tokens=500, model_name="unknown:fake-model-xyz"
        )
        assert tracker.cost() is None

    def test_returns_float_for_known_model(self):
        tracker = UsageTracker(
            input_tokens=1000, output_tokens=500, model_name="anthropic:claude-sonnet-4-6"
        )
        cost = tracker.cost()
        assert cost is not None
        assert isinstance(cost, float)
        assert cost > 0

    def test_parses_provider_model_format(self):
        tracker = UsageTracker(input_tokens=1000, output_tokens=500, model_name="openai:gpt-4o")
        cost = tracker.cost()
        assert cost is not None
        assert cost > 0

    def test_works_without_provider_prefix(self):
        tracker = UsageTracker(input_tokens=1000, output_tokens=500, model_name="gpt-4o")
        cost = tracker.cost()
        assert cost is not None
        assert cost > 0


class TestUsageTrackerFormat:
    def test_format_tokens(self):
        tracker = UsageTracker(input_tokens=12_345, output_tokens=678)
        assert tracker.format_tokens() == "12.3k in, 678 out"

    def test_format_cost_known_model(self):
        tracker = UsageTracker(
            input_tokens=1000, output_tokens=500, model_name="anthropic:claude-sonnet-4-6"
        )
        cost_str = tracker.format_cost()
        assert cost_str.startswith("$")

    def test_format_cost_unknown_model(self):
        tracker = UsageTracker(
            input_tokens=1000, output_tokens=500, model_name="unknown:fake-model"
        )
        assert tracker.format_cost() == "?"

    def test_format_cost_small_amount(self):
        tracker = UsageTracker(
            input_tokens=10, output_tokens=5, model_name="anthropic:claude-sonnet-4-6"
        )
        cost_str = tracker.format_cost()
        # Small amounts use 4 decimal places
        assert cost_str.startswith("$0.00")

    def test_format_usage_combines_tokens_and_cost(self):
        tracker = UsageTracker(
            input_tokens=1000, output_tokens=500, model_name="unknown:fake-model"
        )
        usage_str = tracker.format_usage()
        assert "1.0k in" in usage_str
        assert "500 out" in usage_str
        assert "?" in usage_str


class TestRunAgentSyncTracker:
    def test_populates_tracker_on_success(self):
        mock_usage = RunUsage(input_tokens=100, output_tokens=50, requests=1)
        mock_result = MagicMock()
        mock_result.usage.return_value = mock_usage

        agent = MagicMock()
        agent.run_sync.return_value = mock_result

        tracker = UsageTracker()
        run_agent_sync(
            agent,
            "prompt",
            operation="test",
            model_name="anthropic:claude-sonnet-4-6",
            tracker=tracker,
        )

        assert tracker.input_tokens == 100
        assert tracker.output_tokens == 50
        assert tracker.requests == 1
        assert tracker.model_name == "anthropic:claude-sonnet-4-6"

    def test_tracker_not_populated_on_error(self):
        agent = MagicMock()
        agent.run_sync.side_effect = AgentRunError("fail")

        tracker = UsageTracker()
        with pytest.raises(LLMRunError):
            run_agent_sync(agent, "prompt", operation="test", tracker=tracker)

        assert tracker.input_tokens == 0
        assert tracker.requests == 0


class TestTaskResultSummary:
    def test_includes_usage_in_summary(self):
        result = TaskResult(
            success=True,
            file_count=2,
            total_lines=100,
            elapsed=5.0,
            usage=UsageTracker(input_tokens=1000, output_tokens=500, model_name="unknown:model"),
        )
        summary = result.summary()
        assert "2 files" in summary
        assert "100 lines" in summary
        assert "5.0s" in summary
        assert "1.0k in" in summary
        assert "500 out" in summary
