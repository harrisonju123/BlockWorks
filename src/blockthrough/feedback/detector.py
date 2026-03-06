"""Background detector for implicit feedback signals (retry, override).

Follows the AlertChecker pattern: background task with run() loop,
shutdown via asyncio.Event, configurable interval.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import asyncpg

from blockthrough.feedback.types import FeedbackSignal, SIGNAL_DEFAULTS
from blockthrough.utils import utcnow

logger = logging.getLogger(__name__)

# Signal configs to detect, each with its SQL query
_SIGNAL_QUERIES: list[tuple[FeedbackSignal, str]] = []

# SQL to detect retries: same session + same prompt_hash within 60s, same model
_RETRY_SQL = """
WITH ordered AS (
    SELECT
        id, created_at, session_id, prompt_hash, model, task_type,
        LAG(created_at) OVER (
            PARTITION BY session_id, prompt_hash, model
            ORDER BY created_at
        ) AS prev_at
    FROM llm_events
    WHERE created_at > $1
      AND session_id IS NOT NULL
      AND prompt_hash IS NOT NULL
)
SELECT id, model, task_type
FROM ordered
WHERE prev_at IS NOT NULL
  AND created_at - prev_at < INTERVAL '60 seconds'
"""

# SQL to detect overrides: same session + same prompt_hash within 60s, different model
_OVERRIDE_SQL = """
WITH ordered AS (
    SELECT
        id, created_at, session_id, prompt_hash, model, task_type,
        LAG(model) OVER (
            PARTITION BY session_id, prompt_hash
            ORDER BY created_at
        ) AS prev_model,
        LAG(created_at) OVER (
            PARTITION BY session_id, prompt_hash
            ORDER BY created_at
        ) AS prev_at
    FROM llm_events
    WHERE created_at > $1
      AND session_id IS NOT NULL
      AND prompt_hash IS NOT NULL
)
SELECT id, model, task_type
FROM ordered
WHERE prev_model IS NOT NULL
  AND prev_model != model
  AND prev_at IS NOT NULL
  AND created_at - prev_at < INTERVAL '60 seconds'
"""

_INSERT_SQL = """
INSERT INTO feedback_signals (id, created_at, event_id, model, task_type, signal, quality_delta, weight, source)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'implicit')
ON CONFLICT (event_id, signal, created_at) DO NOTHING
"""

# Wire signal queries after SQL definitions
_SIGNAL_QUERIES.extend([
    (FeedbackSignal.RETRY, _RETRY_SQL),
    (FeedbackSignal.OVERRIDE, _OVERRIDE_SQL),
])


class FeedbackDetector:
    """Detect implicit feedback signals from LLM event patterns."""

    def __init__(
        self,
        db_url: str,
        *,
        detection_interval_s: int = 300,
    ) -> None:
        # Normalize: asyncpg uses postgresql:// not postgresql+asyncpg://
        self._db_url = db_url.replace("postgresql+asyncpg", "postgresql", 1) if "postgresql+asyncpg" in db_url else db_url
        self._detection_interval_s = detection_interval_s
        self._shutdown_event = asyncio.Event()
        self._pool: asyncpg.Pool | None = None
        self._last_detection_at = utcnow()

    async def shutdown(self) -> None:
        """Signal the detector loop to stop."""
        self._shutdown_event.set()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._db_url, min_size=1, max_size=2)
        return self._pool

    async def run(self) -> None:
        """Main detection loop."""
        logger.info("FeedbackDetector started (interval=%ds)", self._detection_interval_s)
        try:
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self._detection_interval_s,
                    )
                    break  # shutdown was signaled
                except asyncio.TimeoutError:
                    pass  # interval elapsed, do detection

                try:
                    await self._detect()
                except Exception:
                    logger.exception("FeedbackDetector detection cycle failed")
        finally:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None

    async def _detect(self) -> None:
        """Run retry and override detection queries."""
        pool = await self._ensure_pool()
        now = utcnow()
        high_water = self._last_detection_at
        counts: dict[str, int] = {}

        async with pool.acquire() as conn:
            for signal, sql in _SIGNAL_QUERIES:
                rows = await conn.fetch(sql, high_water)
                delta, weight = SIGNAL_DEFAULTS[signal]
                if rows:
                    await conn.executemany(
                        _INSERT_SQL,
                        [
                            (
                                uuid.uuid4(), now, row["id"],
                                row["model"], row["task_type"] or "unknown",
                                signal.value, delta, weight,
                            )
                            for row in rows
                        ],
                    )
                counts[signal.value] = len(rows)

            logger.debug(
                "FeedbackDetector: %s detected since %s",
                counts, high_water.isoformat(),
            )

        self._last_detection_at = now
