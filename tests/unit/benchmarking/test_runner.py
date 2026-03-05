"""Tests for the benchmark runner."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from blockthrough.benchmarking.eval_set import EvalPrompt, to_messages
from blockthrough.benchmarking.runner import (
    EVAL_ORG_ID,
    BenchmarkRunner,
    PromptResult,
    _EVAL_EVENT_ID,
)
from blockthrough.pipeline.hasher import hash_content
from blockthrough.types import TaskType


def _make_prompt(task_type: str = "code_generation") -> EvalPrompt:
    sys = f"You are a {task_type} assistant."
    usr = f"Do a {task_type} task: {uuid.uuid4().hex[:8]}"
    return EvalPrompt(
        system_prompt=sys,
        user_prompt=usr,
        task_type=TaskType(task_type),
        prompt_hash=hash_content(
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}]
        ),
    )


class TestBenchmarkRunner:
    def test_is_done_empty(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
        )
        prompt = _make_prompt()
        assert not runner._is_done(prompt.prompt_hash, "claude-haiku-4-5-20251001")

    def test_is_done_after_mark(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
        )
        prompt = _make_prompt()
        runner._completed_pairs.add((prompt.prompt_hash, "claude-haiku-4-5-20251001"))
        assert runner._is_done(prompt.prompt_hash, "claude-haiku-4-5-20251001")

    @pytest.mark.asyncio
    async def test_estimate_cost_all_new(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        )
        runner._resume_loaded = True  # skip DB hit
        prompts = [_make_prompt("code_generation"), _make_prompt("classification")]
        estimate = await runner.estimate_cost(prompts)

        assert estimate["total_pairs"] == 4  # 2 prompts x 2 models
        assert estimate["unique_prompts"] == 2
        assert estimate["total_cost_estimate"] > 0
        assert estimate["skipped_already_done"] == 0

    @pytest.mark.asyncio
    async def test_estimate_cost_with_completed(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        )
        runner._resume_loaded = True
        prompt = _make_prompt("code_generation")
        runner._completed_pairs.add((prompt.prompt_hash, "claude-sonnet-4-6"))

        estimate = await runner.estimate_cost([prompt])
        assert estimate["total_pairs"] == 1  # only haiku remaining

    def test_eval_event_id_is_zero_uuid(self):
        assert str(_EVAL_EVENT_ID) == "00000000-0000-0000-0000-000000000000"

    def test_eval_org_id_sentinel(self):
        assert EVAL_ORG_ID == "eval-v1"


class TestPromptResult:
    def test_fields(self):
        r = PromptResult(
            prompt_hash="abc123",
            benchmark_model="claude-haiku-4-5-20251001",
            task_type=TaskType.CODE_GENERATION,
            quality_score=0.85,
            benchmark_cost=0.001,
            benchmark_latency_ms=500.0,
            reference_cost=0.01,
            reference_latency_ms=2000.0,
            judge_model="claude-sonnet-4-6",
            rubric_version="1.0",
        )
        assert r.quality_score == 0.85
        assert r.task_type == TaskType.CODE_GENERATION


class TestRunnerResumeLogic:
    @pytest.mark.asyncio
    async def test_load_resume_state_populates_pairs(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
        )

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[
            {"original_model": "hash1", "benchmark_model": "claude-haiku-4-5-20251001"},
            {"original_model": "hash2", "benchmark_model": "claude-sonnet-4-6"},
        ])
        runner._pool = mock_pool

        await runner.load_resume_state()

        assert ("hash1", "claude-haiku-4-5-20251001") in runner._completed_pairs
        assert ("hash2", "claude-sonnet-4-6") in runner._completed_pairs
        assert len(runner._completed_pairs) == 2
        assert runner._resume_loaded

    @pytest.mark.asyncio
    async def test_load_resume_state_idempotent(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
        )

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        runner._pool = mock_pool

        await runner.load_resume_state()
        await runner.load_resume_state()  # second call is no-op

        assert mock_pool.fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_run_skips_all_completed(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
        )

        prompt = _make_prompt("classification")
        runner._completed_pairs = {(prompt.prompt_hash, "claude-haiku-4-5-20251001")}
        runner._resume_loaded = True

        stats = await runner.run([prompt])
        assert stats.completed == 0
        assert stats.skipped == 1


class TestRunnerBenchmarkOne:
    @pytest.mark.asyncio
    async def test_benchmark_one_success(self):
        import asyncio
        import json

        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
            concurrency=1,
        )
        runner._semaphore = asyncio.Semaphore(1)

        prompt = _make_prompt("classification")
        messages = to_messages(prompt)
        prompt_text = json.dumps(messages)

        with patch(
            "blockthrough.benchmarking.runner._replay_prompt",
            new_callable=AsyncMock,
            return_value=("benchmark response", 0.001, 300.0),
        ), patch(
            "blockthrough.benchmarking.runner.evaluate",
            new_callable=AsyncMock,
            return_value=(0.88, "1.0"),
        ):
            result = await runner._benchmark_one(
                prompt, "claude-haiku-4-5-20251001",
                messages, prompt_text,
                "reference response", 0.01, 1500.0,
                total=1,
            )

        assert result is not None
        assert result.quality_score == 0.88
        assert result.benchmark_model == "claude-haiku-4-5-20251001"
        assert result.task_type == TaskType.CLASSIFICATION
        assert runner._stats.completed == 1

    @pytest.mark.asyncio
    async def test_benchmark_one_auth_error(self):
        import asyncio
        import json
        import litellm

        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["gpt-4o"],
            concurrency=1,
        )
        runner._semaphore = asyncio.Semaphore(1)

        prompt = _make_prompt("summarization")
        messages = to_messages(prompt)
        prompt_text = json.dumps(messages)

        with patch(
            "blockthrough.benchmarking.runner._replay_prompt",
            new_callable=AsyncMock,
            side_effect=litellm.AuthenticationError(
                message="bad key",
                llm_provider="openai",
                model="gpt-4o",
            ),
        ):
            result = await runner._benchmark_one(
                prompt, "gpt-4o",
                messages, prompt_text,
                "reference response", 0.01, 1500.0,
                total=1,
            )

        assert result is None
        assert runner._stats.failed == 1

    @pytest.mark.asyncio
    async def test_get_reference_caches(self):
        runner = BenchmarkRunner(
            db_url="postgresql://test:test@localhost/test",
            benchmark_models=["claude-haiku-4-5-20251001"],
        )

        prompt = _make_prompt("code_generation")
        messages = to_messages(prompt)
        call_count = 0

        async def mock_replay(msgs, model, **kw):
            nonlocal call_count
            call_count += 1
            return ("completion", 0.01, 1000.0)

        with patch("blockthrough.benchmarking.runner._replay_prompt", side_effect=mock_replay):
            r1 = await runner._get_reference(prompt, messages)
            r2 = await runner._get_reference(prompt, messages)

        assert call_count == 1  # only called once, second was cached
        assert r1 == r2
