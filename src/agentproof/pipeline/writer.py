"""Background event writer that batches inserts to TimescaleDB.

Drains the in-memory queue and flushes in batches (by count or
time interval, whichever comes first) to minimize DB round trips.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import asyncpg

from agentproof.types import LLMEvent

logger = logging.getLogger(__name__)

_EVENT_COLUMNS = [
    "id", "created_at", "status",
    "provider", "model", "model_group",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "estimated_cost", "custom_pricing",
    "latency_ms", "time_to_first_token_ms",
    "prompt_hash", "completion_hash", "system_prompt_hash",
    "session_id", "trace_id", "span_id", "parent_span_id",
    "agent_framework", "agent_name",
    "has_tool_calls",
    "task_type", "task_type_confidence",
    "error_type", "error_message_hash",
    "litellm_call_id", "api_base", "org_id", "user_id", "custom_metadata",
]

_TOOL_CALL_COLUMNS = [
    "id", "event_id", "created_at", "tool_name", "args_hash", "response_summary_hash",
]

MAX_RETRIES = 3


class EventWriter:
    """Consumes LLMEvents from a queue and batch-inserts into Postgres."""

    def __init__(
        self,
        db_url: str,
        queue: asyncio.Queue[LLMEvent],
        batch_size: int = 50,
        flush_interval_s: float = 0.1,
    ) -> None:
        # Convert SQLAlchemy URL to asyncpg format
        self._db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        self._queue = queue
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._db_url, min_size=2, max_size=10
            )
        return self._pool

    async def run(self) -> None:
        """Main loop: drain queue, batch, flush."""
        pool = await self._ensure_pool()
        batch: list[LLMEvent] = []

        while True:
            try:
                # Wait for first event or timeout
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=self._flush_interval_s
                    )
                    batch.append(event)
                except asyncio.TimeoutError:
                    pass

                # Drain whatever else is ready
                while len(batch) < self._batch_size:
                    try:
                        event = self._queue.get_nowait()
                        batch.append(event)
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    await self._flush_with_retry(pool, batch)
                    batch = []

            except Exception:
                logger.exception("EventWriter unexpected error — events may be lost")
                batch = []
                await asyncio.sleep(1.0)

    async def _flush_with_retry(
        self, pool: asyncpg.Pool, batch: list[LLMEvent]
    ) -> None:
        """Attempt batch flush with retries. On persistent failure, try individual inserts."""
        for attempt in range(MAX_RETRIES):
            try:
                await self._flush(pool, batch)
                return
            except (asyncpg.PostgresConnectionError, OSError) as e:
                logger.warning(
                    "Flush attempt %d/%d failed (transient): %s",
                    attempt + 1, MAX_RETRIES, e,
                )
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception:
                logger.exception("Flush failed with non-transient error")
                break

        # Batch flush failed — try individual inserts to isolate bad events
        logger.warning("Batch flush failed, falling back to individual inserts")
        for event in batch:
            try:
                await self._flush(pool, [event])
            except Exception:
                logger.exception("Failed to write event %s — dropping", event.id)

    async def _flush(self, pool: asyncpg.Pool, batch: list[LLMEvent]) -> None:
        """Write a batch of events using COPY for throughput."""
        event_rows: list[tuple[Any, ...]] = []
        tool_call_rows: list[tuple[Any, ...]] = []

        for event in batch:
            event_rows.append((
                event.id,
                event.created_at,
                event.status.value,
                event.provider,
                event.model,
                event.model_group,
                event.prompt_tokens,
                event.completion_tokens,
                event.total_tokens,
                event.estimated_cost,
                event.custom_pricing,
                event.latency_ms,
                event.time_to_first_token_ms,
                event.prompt_hash,
                event.completion_hash,
                event.system_prompt_hash,
                event.session_id,
                event.trace_id,
                event.span_id,
                event.parent_span_id,
                event.agent_framework,
                event.agent_name,
                event.has_tool_calls,
                event.task_type.value if event.task_type else None,
                event.task_type_confidence,
                event.error_type,
                event.error_message_hash,
                event.litellm_call_id,
                event.api_base,
                event.org_id,
                event.user_id,
                json.dumps(event.custom_metadata) if event.custom_metadata else None,
            ))

            for tc in event.tool_calls:
                tool_call_rows.append((
                    uuid.uuid4(),
                    event.id,
                    event.created_at,
                    tc.tool_name,
                    tc.args_hash,
                    tc.response_summary_hash,
                ))

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.copy_records_to_table(
                    "llm_events", records=event_rows, columns=_EVENT_COLUMNS
                )
                if tool_call_rows:
                    await conn.copy_records_to_table(
                        "tool_calls", records=tool_call_rows, columns=_TOOL_CALL_COLUMNS
                    )

        logger.debug("Flushed %d events to TimescaleDB", len(batch))
