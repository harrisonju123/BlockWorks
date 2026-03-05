"""Waste analyzer — orchestrates all five detectors.

Queries the DB for events, tool calls, traces, and the fitness matrix,
runs each detector, merges results into a WasteReport, and computes
the overall waste score as total_savings / total_spend.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from blockthrough.db.queries import (
    get_duplicate_tool_calls,
    get_fitness_matrix,
    get_prompt_hash_duplicates,
    get_trace_tool_patterns,
    get_waste_analysis,
)
from blockthrough.waste.detectors.agent_loops import detect_agent_loops
from blockthrough.waste.detectors.cache_misses import detect_cache_misses
from blockthrough.waste.detectors.context_bloat import detect_context_bloat
from blockthrough.waste.detectors.model_overkill import detect_model_overkill
from blockthrough.waste.detectors.redundant_calls import detect_redundant_calls
from blockthrough.utils import utcnow
from blockthrough.waste.types import WasteItem, WasteReport

logger = logging.getLogger(__name__)


class WasteAnalyzer:
    """Runs all waste detectors and produces a unified WasteReport."""

    def __init__(
        self,
        *,
        cache_window_hours: int = 1,
        quality_threshold: float = 0.90,
    ) -> None:
        self._cache_window_hours = cache_window_hours
        self._quality_threshold = quality_threshold

    async def analyze(
        self,
        session: AsyncSession,
        start: datetime,
        end: datetime,
        *,
        org_id: str | None = None,
        raw_events: list[dict] | None = None,
    ) -> WasteReport:
        """Run all detectors and merge into a single report.

        Pass raw_events (with prompt_tokens/completion_tokens per event)
        to enable context bloat detection. Without them, that detector is skipped.
        """
        # Fire all independent DB queries concurrently
        (
            usage_rows,
            fitness_entries,
            duplicate_tool_rows,
            prompt_dup_rows,
            trace_patterns,
        ) = await asyncio.gather(
            get_waste_analysis(session, start, end, org_id),
            get_fitness_matrix(session, org_id),
            get_duplicate_tool_calls(session, start, end),
            get_prompt_hash_duplicates(
                session, start, end, window_hours=self._cache_window_hours
            ),
            get_trace_tool_patterns(session, start, end),
        )

        total_spend = sum(float(r.get("total_cost") or 0) for r in usage_rows)

        all_items: list[WasteItem] = []
        all_items.extend(
            detect_model_overkill(
                usage_rows, fitness_entries, quality_threshold=self._quality_threshold
            )
        )
        all_items.extend(detect_redundant_calls(duplicate_tool_rows))
        all_items.extend(detect_context_bloat(raw_events or []))
        all_items.extend(detect_cache_misses(prompt_dup_rows))
        all_items.extend(detect_agent_loops(trace_patterns))

        all_items.sort(key=lambda i: i.savings, reverse=True)

        total_savings = sum(i.savings for i in all_items)
        waste_score = min(total_savings / total_spend, 1.0) if total_spend > 0 else 0.0

        return WasteReport(
            items=all_items,
            total_savings=round(total_savings, 6),
            total_spend=round(total_spend, 6),
            waste_score=round(waste_score, 6),
            generated_at=utcnow(),
        )
