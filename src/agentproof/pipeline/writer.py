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

from agentproof.pipeline.base_worker import AsyncQueueWorker
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


class EventWriter(AsyncQueueWorker[LLMEvent]):
    """Consumes LLMEvents from a queue and batch-inserts into Postgres."""

    def __init__(
        self,
        db_url: str,
        queue: asyncio.Queue[LLMEvent],
        batch_size: int = 50,
        flush_interval_s: float = 0.1,
    ) -> None:
        super().__init__(
            db_url=db_url,
            queue=queue,
            batch_size=batch_size,
            flush_interval_s=flush_interval_s,
            pool_min=2,
            pool_max=10,
        )

    def _make_item_id(self, item: LLMEvent) -> str:
        return str(item.id)

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
