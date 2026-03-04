"""Background writer that batch-inserts routing decisions to TimescaleDB.

Follows the same AsyncQueueWorker pattern as EventWriter — drains a queue,
batches into COPY records, and flushes with retry logic.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

import asyncpg

from agentproof.pipeline.base_worker import AsyncQueueWorker
from agentproof.utils import utcnow

logger = logging.getLogger(__name__)

_DECISION_COLUMNS = [
    "id",
    "created_at",
    "task_type",
    "requested_model",
    "selected_model",
    "was_overridden",
    "reason",
    "policy_version",
    "group_name",
]


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Queue item bundling a routing decision with its context.

    RoutingDecision alone lacks task_type, requested_model, and policy_version
    because those are caller context, not decision output. This wrapper carries
    all the fields needed for the DB row.
    """

    task_type: str | None
    requested_model: str
    selected_model: str
    was_overridden: bool
    reason: str
    policy_version: int | None
    group_name: str | None


class RoutingDecisionWriter(AsyncQueueWorker[DecisionRecord]):
    """Consumes DecisionRecords from a queue and batch-inserts into routing_decisions."""

    def __init__(
        self,
        db_url: str,
        queue: asyncio.Queue[DecisionRecord],
        batch_size: int = 50,
        flush_interval_s: float = 0.5,
    ) -> None:
        super().__init__(
            db_url=db_url,
            queue=queue,
            batch_size=batch_size,
            flush_interval_s=flush_interval_s,
            pool_min=1,
            pool_max=3,
        )

    def _make_item_id(self, item: DecisionRecord) -> str:
        return f"{item.selected_model}:{item.reason[:30] if item.reason else 'no-reason'}"

    async def _flush(self, pool: asyncpg.Pool, batch: list[DecisionRecord]) -> None:
        """Write a batch of routing decisions using COPY for throughput."""
        now = utcnow()
        rows = []
        for rec in batch:
            rows.append((
                uuid.uuid4(),
                now,
                rec.task_type,
                rec.requested_model,
                rec.selected_model,
                rec.was_overridden,
                rec.reason,
                rec.policy_version,
                rec.group_name,
            ))

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.copy_records_to_table(
                    "routing_decisions",
                    records=rows,
                    columns=_DECISION_COLUMNS,
                )

        logger.debug("Flushed %d routing decisions to TimescaleDB", len(batch))
