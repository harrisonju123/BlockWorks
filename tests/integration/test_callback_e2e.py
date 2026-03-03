"""Integration tests: Callback -> DB round trip.

Verifies the full pipeline works end-to-end:
  callback -> queue -> writer -> TimescaleDB -> query verification

Uses testcontainers to spin up a real TimescaleDB instance so we
test actual SQL, COPY protocol, hypertable behavior, etc.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from agentproof.pipeline.callback import AgentProofCallback

from .conftest import make_litellm_kwargs


pytestmark = pytest.mark.integration


@pytest.fixture
def callback(db_url: str, _apply_schema) -> AgentProofCallback:
    """Create a callback instance pointed at the test DB."""
    return AgentProofCallback(
        db_url=db_url,
        org_id="test-org",
        enable_classification=True,
        batch_size=10,
        flush_interval_ms=50,
    )


async def _wait_for_flush(pool: asyncpg.Pool, expected: int, timeout_s: float = 5.0) -> int:
    """Poll the DB until we see the expected event count or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    count = 0
    while asyncio.get_event_loop().time() < deadline:
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM llm_events")
        if count >= expected:
            return count
        await asyncio.sleep(0.05)
    return count


class TestSuccessEvent:
    """Verify a single successful LLM call flows through the pipeline."""

    async def test_event_persisted(self, callback: AgentProofCallback, clean_db: asyncpg.Pool):
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs()

        await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)

        count = await _wait_for_flush(clean_db, expected=1)
        assert count == 1

    async def test_field_values(self, callback: AgentProofCallback, clean_db: asyncpg.Pool):
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs(
            model="gpt-4o",
            provider="openai",
            prompt_tokens=200,
            completion_tokens=100,
            cost=0.005,
        )

        await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)
        await _wait_for_flush(clean_db, expected=1)

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM llm_events LIMIT 1")

        assert row["model"] == "gpt-4o"
        assert row["provider"] == "openai"
        assert row["prompt_tokens"] == 200
        assert row["completion_tokens"] == 100
        assert row["total_tokens"] == 300
        assert row["status"] == "success"
        assert row["org_id"] == "test-org"
        assert abs(row["estimated_cost"] - 0.005) < 1e-6

    async def test_hashes_populated(self, callback: AgentProofCallback, clean_db: asyncpg.Pool):
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs()

        await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)
        await _wait_for_flush(clean_db, expected=1)

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM llm_events LIMIT 1")

        # Hashes should be 64-char hex (SHA-256)
        assert row["prompt_hash"] is not None
        assert len(row["prompt_hash"]) == 64
        assert row["completion_hash"] is not None
        assert len(row["completion_hash"]) == 64
        assert row["system_prompt_hash"] is not None
        assert len(row["system_prompt_hash"]) == 64

    async def test_classification_populated(
        self, callback: AgentProofCallback, clean_db: asyncpg.Pool
    ):
        """The classifier should set task_type based on system prompt signals."""
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs(
            messages=[
                {"role": "system", "content": "You are a code assistant. Write code and implement functions."},
                {"role": "user", "content": "Implement a binary search function."},
            ],
        )

        await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)
        await _wait_for_flush(clean_db, expected=1)

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow("SELECT task_type, task_type_confidence FROM llm_events LIMIT 1")

        assert row["task_type"] is not None
        assert row["task_type_confidence"] is not None
        assert row["task_type_confidence"] > 0


class TestFailureEvent:
    """Verify failure events are persisted with error context."""

    async def test_failure_persisted(self, callback: AgentProofCallback, clean_db: asyncpg.Pool):
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs(
            exception=ValueError("Rate limit exceeded"),
        )

        await callback.async_log_failure_event(kwargs, response_obj, start_time, end_time)
        await _wait_for_flush(clean_db, expected=1)

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM llm_events LIMIT 1")

        assert row["status"] == "failure"
        assert row["error_type"] == "ValueError"
        assert row["error_message_hash"] is not None
        assert len(row["error_message_hash"]) == 64


class TestToolCalls:
    """Verify tool calls are written to the normalized tool_calls table."""

    async def test_tool_calls_persisted(
        self, callback: AgentProofCallback, clean_db: asyncpg.Pool
    ):
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs(
            tool_calls=[
                {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                {"name": "search_db", "arguments": '{"query": "users"}'},
            ],
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )

        await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)
        await _wait_for_flush(clean_db, expected=1)

        async with clean_db.acquire() as conn:
            event_row = await conn.fetchrow("SELECT * FROM llm_events LIMIT 1")
            tool_rows = await conn.fetch(
                "SELECT * FROM tool_calls WHERE event_id = $1 ORDER BY tool_name",
                event_row["id"],
            )

        assert event_row["has_tool_calls"] is True
        assert len(tool_rows) == 2
        assert tool_rows[0]["tool_name"] == "get_weather"
        assert tool_rows[1]["tool_name"] == "search_db"
        # args_hash should be a SHA-256 hex digest
        assert len(tool_rows[0]["args_hash"]) == 64


class TestBatchFlush:
    """Verify that multiple events are batched and flushed correctly."""

    async def test_multiple_events(self, callback: AgentProofCallback, clean_db: asyncpg.Pool):
        for i in range(15):
            kwargs, response_obj, start_time, end_time = make_litellm_kwargs(
                model=f"model-{i % 3}",
                litellm_call_id=f"batch-call-{i}",
            )
            await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)

        count = await _wait_for_flush(clean_db, expected=15, timeout_s=10.0)
        assert count == 15

        async with clean_db.acquire() as conn:
            models = await conn.fetch(
                "SELECT DISTINCT model FROM llm_events ORDER BY model"
            )
        assert len(models) == 3


class TestQueueFull:
    """Verify the callback doesn't crash when the queue is full."""

    async def test_queue_full_no_crash(self, db_url: str, _apply_schema):
        """Fill the queue beyond capacity and verify graceful degradation."""
        cb = AgentProofCallback(
            db_url=db_url,
            org_id="test-org",
            enable_classification=False,
            batch_size=10,
            flush_interval_ms=50,
        )
        # Override queue to a tiny capacity
        cb._queue = asyncio.Queue(maxsize=5)

        # Enqueue without starting the writer — queue will fill
        kwargs, response_obj, start_time, end_time = make_litellm_kwargs()

        # First call starts the writer, but we keep pushing fast enough to fill
        for _ in range(20):
            await cb.async_log_success_event(kwargs, response_obj, start_time, end_time)

        # No exception should have been raised. The queue drops events
        # when full instead of blocking or crashing.
        assert True


class TestMixedEvents:
    """Verify success and failure events coexist correctly."""

    async def test_mixed_success_and_failure(
        self, callback: AgentProofCallback, clean_db: asyncpg.Pool
    ):
        # 3 success events
        for _ in range(3):
            kwargs, resp, st, et = make_litellm_kwargs()
            await callback.async_log_success_event(kwargs, resp, st, et)

        # 2 failure events
        for _ in range(2):
            kwargs, resp, st, et = make_litellm_kwargs(
                exception=TimeoutError("Gateway timeout"),
            )
            await callback.async_log_failure_event(kwargs, resp, st, et)

        count = await _wait_for_flush(clean_db, expected=5)
        assert count == 5

        async with clean_db.acquire() as conn:
            success_count = await conn.fetchval(
                "SELECT COUNT(*) FROM llm_events WHERE status = 'success'"
            )
            failure_count = await conn.fetchval(
                "SELECT COUNT(*) FROM llm_events WHERE status = 'failure'"
            )

        assert success_count == 3
        assert failure_count == 2
