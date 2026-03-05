"""Background writer for MCP tracing data.

Follows the same async queue-and-flush pattern as EventWriter:
a background task drains MCPCall objects from a queue and batch-
inserts them into the mcp_calls and mcp_execution_graph tables.

Uses dual queues (calls + edges), so it overrides the base run() loop
while inheriting pool management and shutdown from AsyncQueueWorker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

from blockthrough.mcp.types import MCPCall, MCPExecutionEdge
from blockthrough.pipeline.base_worker import MAX_RETRIES, AsyncQueueWorker

logger = logging.getLogger(__name__)

_MCP_CALL_COLUMNS = [
    "id", "created_at", "event_id", "trace_id",
    "server_name", "method", "params_hash", "response_hash",
    "latency_ms", "response_tokens", "status", "error_type",
]

_MCP_EDGE_COLUMNS = [
    "id", "parent_call_id", "child_call_id", "trace_id",
]


class MCPWriter(AsyncQueueWorker[MCPCall]):
    """Consumes MCPCalls and MCPExecutionEdges from queues and batch-inserts into Postgres.

    Inherits pool management and shutdown from AsyncQueueWorker. Overrides
    run() because it drains two queues (calls + edges) in each iteration.
    """

    def __init__(
        self,
        db_url: str,
        call_queue: asyncio.Queue[MCPCall],
        edge_queue: asyncio.Queue[MCPExecutionEdge],
        batch_size: int = 50,
        flush_interval_s: float = 0.1,
    ) -> None:
        # The base class gets the call_queue as the primary queue
        super().__init__(
            db_url=db_url,
            queue=call_queue,
            batch_size=batch_size,
            flush_interval_s=flush_interval_s,
            pool_min=2,
            pool_max=5,
        )
        self._call_queue = call_queue
        self._edge_queue = edge_queue

    def _make_item_id(self, item: MCPCall) -> str:
        return str(item.id)

    async def _flush(self, pool: asyncpg.Pool, batch: list[MCPCall]) -> None:
        """Write a batch of MCP calls. Used by _flush_calls_with_retry fallback path."""
        await self._flush_both(pool, batch, [])

    async def _flush_both(
        self,
        pool: asyncpg.Pool,
        calls: list[MCPCall],
        edges: list[MCPExecutionEdge],
    ) -> None:
        """Write a batch of MCP calls and edges using COPY for throughput."""
        call_rows: list[tuple[Any, ...]] = []
        edge_rows: list[tuple[Any, ...]] = []

        for call in calls:
            call_rows.append((
                call.id,
                call.created_at,
                call.event_id,
                call.trace_id,
                call.server_name,
                call.method,
                call.params_hash,
                call.response_hash,
                call.latency_ms,
                call.response_tokens,
                call.status.value,
                call.error_type,
            ))

        for edge in edges:
            edge_rows.append((
                edge.id,
                edge.parent_call_id,
                edge.child_call_id,
                edge.trace_id,
            ))

        async with pool.acquire() as conn:
            async with conn.transaction():
                if call_rows:
                    await conn.copy_records_to_table(
                        "mcp_calls", records=call_rows, columns=_MCP_CALL_COLUMNS
                    )
                if edge_rows:
                    await conn.copy_records_to_table(
                        "mcp_execution_graph", records=edge_rows, columns=_MCP_EDGE_COLUMNS
                    )

        logger.debug(
            "Flushed %d MCP calls and %d edges to TimescaleDB",
            len(call_rows), len(edge_rows),
        )

    async def _flush_both_with_retry(
        self,
        pool: asyncpg.Pool,
        calls: list[MCPCall],
        edges: list[MCPExecutionEdge],
    ) -> None:
        """Attempt batch flush with retries."""
        for attempt in range(MAX_RETRIES):
            try:
                await self._flush_both(pool, calls, edges)
                return
            except (asyncpg.PostgresConnectionError, OSError) as e:
                logger.warning(
                    "MCP flush attempt %d/%d failed (transient): %s",
                    attempt + 1, MAX_RETRIES, e,
                )
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception:
                logger.exception("MCP flush failed with non-transient error")
                break

        # Batch failed -- try individual inserts to isolate bad records
        logger.warning("MCP batch flush failed, falling back to individual inserts")
        for call in calls:
            try:
                await self._flush_both(pool, [call], [])
            except Exception:
                logger.exception("Failed to write MCP call %s -- dropping", call.id)
        for edge in edges:
            try:
                await self._flush_both(pool, [], [edge])
            except Exception:
                logger.exception(
                    "Failed to write MCP edge %s->%s -- dropping",
                    edge.parent_call_id, edge.child_call_id,
                )

    async def run(self) -> None:
        """Main loop: drain both queues, batch, flush.

        Overrides the base single-queue run() to handle dual queues.
        """
        pool = await self._ensure_pool()
        call_batch: list[MCPCall] = []
        edge_batch: list[MCPExecutionEdge] = []

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for first call or timeout
                    try:
                        call = await asyncio.wait_for(
                            self._call_queue.get(), timeout=self._flush_interval_s
                        )
                        call_batch.append(call)
                    except asyncio.TimeoutError:
                        pass

                    # Drain additional calls up to batch size
                    while len(call_batch) < self._batch_size:
                        try:
                            call_batch.append(self._call_queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break

                    # Drain all available edges
                    while True:
                        try:
                            edge_batch.append(self._edge_queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break

                    if call_batch or edge_batch:
                        await self._flush_both_with_retry(pool, call_batch, edge_batch)
                        call_batch = []
                        edge_batch = []

                except Exception:
                    logger.exception("MCPWriter unexpected error")
                    call_batch = []
                    edge_batch = []
                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.info("MCPWriter cancelled, draining remaining items")

        # Drain phase
        while not self._call_queue.empty():
            try:
                call_batch.append(self._call_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        while not self._edge_queue.empty():
            try:
                edge_batch.append(self._edge_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if call_batch or edge_batch:
            await self._flush_both_with_retry(pool, call_batch, edge_batch)
            logger.info(
                "Drained %d MCP calls and %d edges during shutdown",
                len(call_batch), len(edge_batch),
            )

        await self._close_pool()
        logger.info("MCPWriter shut down cleanly")
