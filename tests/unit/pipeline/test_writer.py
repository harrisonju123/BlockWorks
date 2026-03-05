"""Tests for EventWriter graceful shutdown behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blockthrough.pipeline.writer import EventWriter
from blockthrough.types import EventStatus, LLMEvent


def _make_event(**overrides) -> LLMEvent:
    """Build a minimal LLMEvent, merging any overrides."""
    import uuid
    from datetime import datetime, timezone

    defaults = dict(
        id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        status=EventStatus.SUCCESS,
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        estimated_cost=0.001,
        latency_ms=100.0,
        prompt_hash="p",
        completion_hash="c",
        trace_id="t-1",
        span_id="s-1",
        litellm_call_id="lc-1",
    )
    defaults.update(overrides)
    return LLMEvent(**defaults)


class TestEventWriterShutdown:
    """Verify that shutdown drains queued events before closing the pool."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_event(self) -> None:
        """shutdown() must set the internal stop flag."""
        queue: asyncio.Queue[LLMEvent] = asyncio.Queue()
        writer = EventWriter(db_url="postgresql://x", queue=queue)
        assert not writer._shutdown_event.is_set()
        await writer.shutdown()
        assert writer._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_run_exits_on_shutdown(self) -> None:
        """run() should exit cleanly after shutdown() is called."""
        queue: asyncio.Queue[LLMEvent] = asyncio.Queue()
        writer = EventWriter(db_url="postgresql://x", queue=queue, flush_interval_s=0.01)

        mock_pool = AsyncMock()
        writer._pool = mock_pool
        writer._ensure_pool = AsyncMock(return_value=mock_pool)

        # Start the writer, then immediately signal shutdown
        task = asyncio.create_task(writer.run())
        await asyncio.sleep(0.02)
        await writer.shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        # Pool should be closed during shutdown
        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_drains_remaining_events(self) -> None:
        """Events sitting in the queue at shutdown time must be flushed."""
        queue: asyncio.Queue[LLMEvent] = asyncio.Queue()
        writer = EventWriter(db_url="postgresql://x", queue=queue, flush_interval_s=0.05)

        mock_pool = AsyncMock()
        writer._pool = mock_pool
        writer._ensure_pool = AsyncMock(return_value=mock_pool)

        flushed_batches: list[list[LLMEvent]] = []
        original_flush = writer._flush_with_retry

        async def _capture_flush(pool, batch):
            flushed_batches.append(list(batch))

        writer._flush_with_retry = _capture_flush

        # Start the writer
        task = asyncio.create_task(writer.run())
        await asyncio.sleep(0.02)

        # Enqueue events, then immediately shut down before the loop can pick them up
        events = [_make_event() for _ in range(5)]
        for e in events:
            queue.put_nowait(e)

        await writer.shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        # All 5 events should have been flushed across one or more batches
        total_flushed = sum(len(b) for b in flushed_batches)
        assert total_flushed == 5

    @pytest.mark.asyncio
    async def test_cancelled_error_triggers_drain(self) -> None:
        """If the task is cancelled, run() should still drain and close the pool."""
        queue: asyncio.Queue[LLMEvent] = asyncio.Queue()
        writer = EventWriter(db_url="postgresql://x", queue=queue, flush_interval_s=5.0)

        mock_pool = AsyncMock()
        writer._pool = mock_pool
        writer._ensure_pool = AsyncMock(return_value=mock_pool)

        flushed_events: list[LLMEvent] = []

        async def _capture_flush(pool, batch):
            flushed_events.extend(batch)

        writer._flush_with_retry = _capture_flush

        task = asyncio.create_task(writer.run())
        # Let the loop enter the long wait_for(queue.get(), timeout=5.0)
        await asyncio.sleep(0.05)

        # Enqueue events while the loop is blocked on queue.get
        events = [_make_event() for _ in range(3)]
        for e in events:
            queue.put_nowait(e)

        # Cancel the task -- simulates SIGTERM cancellation.
        # run() catches CancelledError internally, drains, and returns normally.
        task.cancel()
        await asyncio.wait_for(task, timeout=2.0)

        # The drain phase should have flushed everything
        assert len(flushed_events) >= 3
        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_closed_after_drain(self) -> None:
        """The asyncpg pool must be closed after the drain phase completes."""
        queue: asyncio.Queue[LLMEvent] = asyncio.Queue()
        writer = EventWriter(db_url="postgresql://x", queue=queue, flush_interval_s=0.01)

        mock_pool = AsyncMock()
        writer._pool = mock_pool
        writer._ensure_pool = AsyncMock(return_value=mock_pool)
        writer._flush_with_retry = AsyncMock()

        task = asyncio.create_task(writer.run())
        await asyncio.sleep(0.02)
        await writer.shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        mock_pool.close.assert_awaited_once()


class TestCallbackClose:
    """Verify BlockThroughCallback.close() orchestrates writer shutdown."""

    @pytest.mark.asyncio
    async def test_close_calls_shutdown_and_awaits_task(self) -> None:
        """close() should signal shutdown, then wait for the writer task to finish."""
        from blockthrough.pipeline.callback import BlockThroughCallback

        cb = BlockThroughCallback(db_url="postgresql+asyncpg://x")

        # Simulate a running writer
        mock_writer = AsyncMock()
        cb._writer = mock_writer

        done_future = asyncio.get_event_loop().create_future()
        done_future.set_result(None)
        cb._writer_task = done_future

        await cb.close(timeout=1.0)

        mock_writer.shutdown.assert_awaited_once()
        assert cb._writer is None
        assert cb._writer_task is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_writer(self) -> None:
        """close() on a callback that never started writing should be safe."""
        from blockthrough.pipeline.callback import BlockThroughCallback

        cb = BlockThroughCallback(db_url="postgresql+asyncpg://x")
        await cb.close()
        assert cb._writer is None
        assert cb._writer_task is None

    @pytest.mark.asyncio
    async def test_close_cancels_on_timeout(self) -> None:
        """If the writer task exceeds the timeout, close() should cancel it."""
        from blockthrough.pipeline.callback import BlockThroughCallback

        cb = BlockThroughCallback(db_url="postgresql+asyncpg://x")

        mock_writer = AsyncMock()
        cb._writer = mock_writer

        # Create a task that will block forever
        never_done = asyncio.get_event_loop().create_future()
        cb._writer_task = asyncio.ensure_future(never_done)

        await cb.close(timeout=0.05)

        # Should have cleaned up
        assert cb._writer is None
        assert cb._writer_task is None
