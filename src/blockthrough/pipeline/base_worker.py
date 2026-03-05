"""Generic base class for async queue-draining workers.

All three background workers (EventWriter, MCPWriter, BenchmarkWorker) share
pool management and graceful shutdown logic. This base class extracts that
shared pattern so subclasses only implement their flush/processing logic.

For batch-oriented workers (EventWriter), the full run() loop with
_flush_with_retry is also provided. Workers with fundamentally different
processing loops (MCPWriter with dual queues, BenchmarkWorker with per-item
API calls) override run() while still inheriting pool lifecycle and shutdown.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from typing import Generic, TypeVar

import asyncpg

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_RETRIES = 3


class AsyncQueueWorker(abc.ABC, Generic[T]):
    """Base class providing pool management, shutdown, and an optional batch run loop.

    Subclasses must implement:
        _flush(pool, batch)     -- write a batch of items to the DB
        _make_item_id(item)     -- return a string identifier for error logging

    The default run() implements the standard batch-drain-flush loop with
    CancelledError handling. Subclasses with different processing patterns
    can override run() while keeping pool and shutdown for free.
    """

    def __init__(
        self,
        db_url: str,
        queue: asyncio.Queue[T],
        batch_size: int = 50,
        flush_interval_s: float = 0.1,
        pool_min: int = 2,
        pool_max: int = 5,
    ) -> None:
        # Normalize SQLAlchemy URLs to asyncpg format
        self._db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        self._queue = queue
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: asyncpg.Pool | None = None
        self._shutdown_event: asyncio.Event = asyncio.Event()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._db_url, min_size=self._pool_min, max_size=self._pool_max
            )
        return self._pool

    async def shutdown(self) -> None:
        """Signal the worker to drain remaining items and stop."""
        self._shutdown_event.set()

    async def _close_pool(self) -> None:
        """Close the connection pool if it was created."""
        if self._pool:
            await self._pool.close()

    @abc.abstractmethod
    async def _flush(self, pool: asyncpg.Pool, batch: list[T]) -> None:
        """Write a batch of items to the database. Subclasses implement the COPY logic."""
        ...

    @abc.abstractmethod
    def _make_item_id(self, item: T) -> str:
        """Return a human-readable identifier for an item, used in error logs."""
        ...

    async def _flush_with_retry(
        self, pool: asyncpg.Pool, batch: list[T]
    ) -> None:
        """Attempt batch flush with retries. Falls back to individual inserts on failure."""
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

        # Batch flush failed -- try individual inserts to isolate bad items
        logger.warning("Batch flush failed, falling back to individual inserts")
        for item in batch:
            try:
                await self._flush(pool, [item])
            except Exception:
                logger.exception(
                    "Failed to write item %s -- dropping", self._make_item_id(item)
                )

    async def run(self) -> None:
        """Main loop: drain queue, batch, flush.

        This default implementation works for single-queue batch workers.
        Override for different processing patterns (dual queues, per-item API calls).
        """
        pool = await self._ensure_pool()
        batch: list[T] = []

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for first item or timeout
                    try:
                        item = await asyncio.wait_for(
                            self._queue.get(), timeout=self._flush_interval_s
                        )
                        batch.append(item)
                    except asyncio.TimeoutError:
                        pass

                    # Drain whatever else is ready
                    while len(batch) < self._batch_size:
                        try:
                            item = self._queue.get_nowait()
                            batch.append(item)
                        except asyncio.QueueEmpty:
                            break

                    if batch:
                        await self._flush_with_retry(pool, batch)
                        batch = []

                except Exception:
                    logger.exception("%s unexpected error", type(self).__name__)
                    batch = []
                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.info("%s cancelled, draining remaining items", type(self).__name__)

        # Drain phase: flush everything remaining in the queue
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._flush_with_retry(pool, batch)
            logger.info(
                "Drained %d items during shutdown", len(batch)
            )

        await self._close_pool()
        logger.info("%s shut down cleanly", type(self).__name__)
