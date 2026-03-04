"""Tests for the traffic mirroring and sampling logic.

All tests mock litellm to avoid real API calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentproof.benchmarking.mirror import (
    BenchmarkWorker,
    _replay_prompt,
    run_benchmark_for_event,
    should_sample,
)
from agentproof.benchmarking.types import BenchmarkConfig
from agentproof.types import EventStatus, LLMEvent, TaskType


def _make_event(
    task_type: TaskType = TaskType.CODE_GENERATION,
    model: str = "claude-sonnet-4-20250514",
    status: EventStatus = EventStatus.SUCCESS,
) -> LLMEvent:
    return LLMEvent(
        id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        status=status,
        provider="anthropic",
        model=model,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        estimated_cost=0.003,
        latency_ms=1200.0,
        prompt_hash="abc",
        completion_hash="def",
        trace_id="trace-1",
        span_id="span-1",
        litellm_call_id="call-1",
        task_type=task_type,
        task_type_confidence=0.95,
    )


class TestShouldSample:

    def test_zero_rate_never_samples(self) -> None:
        config = BenchmarkConfig(sample_rate=0.0)
        event = _make_event()
        # Run 100 times to verify determinism
        for _ in range(100):
            assert should_sample(event, config) is False

    def test_full_rate_always_samples(self) -> None:
        config = BenchmarkConfig(sample_rate=1.0)
        event = _make_event()
        for _ in range(100):
            assert should_sample(event, config) is True

    def test_failure_events_not_sampled(self) -> None:
        config = BenchmarkConfig(sample_rate=1.0)
        event = _make_event(status=EventStatus.FAILURE)
        assert should_sample(event, config) is False

    def test_unknown_task_type_not_sampled(self) -> None:
        config = BenchmarkConfig(sample_rate=1.0)
        event = _make_event(task_type=TaskType.UNKNOWN)
        assert should_sample(event, config) is False

    def test_none_task_type_not_sampled(self) -> None:
        config = BenchmarkConfig(sample_rate=1.0)
        event = _make_event()
        event.task_type = None
        assert should_sample(event, config) is False

    def test_disabled_task_type_not_sampled(self) -> None:
        config = BenchmarkConfig(
            sample_rate=1.0,
            enabled_task_types=[TaskType.CLASSIFICATION],
        )
        event = _make_event(task_type=TaskType.CODE_GENERATION)
        assert should_sample(event, config) is False

    def test_enabled_task_type_sampled(self) -> None:
        config = BenchmarkConfig(
            sample_rate=1.0,
            enabled_task_types=[TaskType.CODE_GENERATION],
        )
        event = _make_event(task_type=TaskType.CODE_GENERATION)
        assert should_sample(event, config) is True

    def test_partial_rate_produces_statistical_distribution(self) -> None:
        """With a 50% sample rate, roughly half of events should be sampled."""
        config = BenchmarkConfig(sample_rate=0.5)
        event = _make_event()
        sampled_count = sum(1 for _ in range(1000) if should_sample(event, config))
        # Allow wide margin for randomness, but it should be in the right ballpark
        assert 350 < sampled_count < 650, f"Expected ~500, got {sampled_count}"

    def test_anthropic_format_messages_are_sampled(self) -> None:
        """Anthropic-native content-block messages should now be accepted."""
        config = BenchmarkConfig(sample_rate=1.0)
        event = _make_event()
        anthropic_messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tc_1", "name": "run", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc_1", "content": "ok"},
            ]},
        ]
        assert should_sample(event, config, messages=anthropic_messages) is True


class TestRunBenchmarkForEvent:

    @pytest.mark.asyncio
    async def test_benchmarks_against_configured_models(self) -> None:
        event = _make_event(model="claude-sonnet-4-20250514")
        messages = [{"role": "user", "content": "Write hello world"}]
        original_completion = "print('hello world')"
        config = BenchmarkConfig(
            benchmark_models=["claude-haiku-4-5-20251001", "gpt-4o-mini"],
            judge_model="claude-haiku-4-5-20251001",
        )

        mock_replay_response = MagicMock()
        mock_replay_response.choices = [MagicMock()]
        mock_replay_response.choices[0].message.content = "print('hello')"
        mock_replay_response._hidden_params = {"response_cost": 0.001}

        mock_judge_response = MagicMock()
        mock_judge_response.choices = [MagicMock()]
        mock_judge_response.choices[0].message.content = '{"correctness": 0.85, "style": 0.9, "completeness": 0.8}'

        with patch("agentproof.benchmarking.mirror.litellm.acompletion", new_callable=AsyncMock) as mock_replay, \
             patch("agentproof.benchmarking.judge.litellm.acompletion", new_callable=AsyncMock) as mock_judge:
            mock_replay.return_value = mock_replay_response
            mock_judge.return_value = mock_judge_response

            results = await run_benchmark_for_event(event, messages, original_completion, config)

        # Should produce 2 results: one per benchmark model
        assert len(results) == 2
        models_benchmarked = {r.benchmark_model for r in results}
        assert models_benchmarked == {"claude-haiku-4-5-20251001", "gpt-4o-mini"}

        for r in results:
            assert r.original_model == "claude-sonnet-4-20250514"
            assert r.original_event_id == event.id
            assert 0.0 <= r.quality_score <= 1.0
            assert r.task_type == TaskType.CODE_GENERATION
            assert r.org_id is None

    @pytest.mark.asyncio
    async def test_skips_same_model_as_original(self) -> None:
        event = _make_event(model="claude-haiku-4-5-20251001")
        messages = [{"role": "user", "content": "test"}]
        config = BenchmarkConfig(
            benchmark_models=["claude-haiku-4-5-20251001", "gpt-4o-mini"],
        )

        mock_replay = MagicMock()
        mock_replay.choices = [MagicMock()]
        mock_replay.choices[0].message.content = "response"
        mock_replay._hidden_params = {"response_cost": 0.001}

        mock_judge = MagicMock()
        mock_judge.choices = [MagicMock()]
        mock_judge.choices[0].message.content = '{"correctness": 0.9, "style": 0.8, "completeness": 0.7}'

        with patch("agentproof.benchmarking.mirror.litellm.acompletion", new_callable=AsyncMock) as mock_r, \
             patch("agentproof.benchmarking.judge.litellm.acompletion", new_callable=AsyncMock) as mock_j:
            mock_r.return_value = mock_replay
            mock_j.return_value = mock_judge

            results = await run_benchmark_for_event(event, messages, "original", config)

        # Only gpt-4o-mini should be benchmarked (haiku skipped because it's the original model)
        assert len(results) == 1
        assert results[0].benchmark_model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_no_task_type_returns_empty(self) -> None:
        event = _make_event()
        event.task_type = None
        messages = [{"role": "user", "content": "test"}]
        config = BenchmarkConfig()

        results = await run_benchmark_for_event(event, messages, "completion", config)
        assert results == []

    @pytest.mark.asyncio
    async def test_replay_failure_is_logged_not_raised(self) -> None:
        """If one benchmark model fails, the others should still proceed."""
        event = _make_event(model="claude-sonnet-4-20250514")
        messages = [{"role": "user", "content": "test"}]
        config = BenchmarkConfig(
            benchmark_models=["failing-model", "gpt-4o-mini"],
        )

        # Both mirror and judge import litellm and share the same module object,
        # so we patch once at the top-level litellm module.
        async def _route_by_model(*args, **kwargs):
            model = kwargs.get("model", "")
            if model == "failing-model":
                raise ConnectionError("Simulated failure")
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = (
                '{"correctness": 0.9, "style": 0.8, "completeness": 0.7}'
            )
            mock_resp._hidden_params = {"response_cost": 0.001}
            return mock_resp

        with patch("litellm.acompletion", side_effect=_route_by_model):
            results = await run_benchmark_for_event(event, messages, "original", config)

        # Only gpt-4o-mini should succeed; failing-model error is swallowed
        assert len(results) == 1
        assert results[0].benchmark_model == "gpt-4o-mini"


class TestBenchmarkConfig:

    def test_default_config(self) -> None:
        config = BenchmarkConfig()
        assert config.sample_rate == 0.05
        assert "claude-haiku-4-5-20251001" in config.benchmark_models
        assert "gpt-4o-mini" in config.benchmark_models
        assert config.judge_model == "claude-haiku-4-5-20251001"
        assert len(config.enabled_task_types) == len(TaskType)

    def test_sample_rate_validation(self) -> None:
        with pytest.raises(Exception):
            BenchmarkConfig(sample_rate=-0.1)
        with pytest.raises(Exception):
            BenchmarkConfig(sample_rate=1.5)

    def test_valid_custom_config(self) -> None:
        config = BenchmarkConfig(
            sample_rate=0.1,
            benchmark_models=["gpt-4o"],
            enabled_task_types=[TaskType.CODE_GENERATION],
            judge_model="gpt-4o-mini",
        )
        assert config.sample_rate == 0.1
        assert config.benchmark_models == ["gpt-4o"]
        assert config.enabled_task_types == [TaskType.CODE_GENERATION]


class TestReplayPromptConversion:

    @pytest.mark.asyncio
    async def test_converts_anthropic_format_before_calling_litellm(self) -> None:
        """_replay_prompt should convert Anthropic content-block messages to OpenAI format."""
        anthropic_messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]
        system_prompt = "You are a helper"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "hi"
        mock_response._hidden_params = {"response_cost": 0.001}

        with patch("agentproof.benchmarking.mirror.litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            await _replay_prompt(anthropic_messages, "test-model", system_prompt=system_prompt)

            # Verify litellm received OpenAI-format messages with system prepended
            called_messages = mock_acomp.call_args.kwargs["messages"]
            assert called_messages[0] == {"role": "system", "content": "You are a helper"}
            assert called_messages[1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_openai_format_passes_through(self) -> None:
        """Plain OpenAI-format messages should pass through unchanged."""
        openai_messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hi"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "hello"
        mock_response._hidden_params = {"response_cost": 0.001}

        with patch("agentproof.benchmarking.mirror.litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            await _replay_prompt(openai_messages, "test-model")

            called_messages = mock_acomp.call_args.kwargs["messages"]
            assert called_messages == openai_messages
