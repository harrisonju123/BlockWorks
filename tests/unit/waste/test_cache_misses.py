"""Tests for the cache miss detector."""

from __future__ import annotations

import pytest

from blockthrough.waste.detectors.cache_misses import detect_cache_misses
from blockthrough.waste.types import WasteCategory, WasteSeverity


def _prompt_dup_row(
    prompt_hash: str = "abc123def456",
    dup_count: int = 5,
    total_cost: float = 0.50,
    models: list[str] | None = None,
    trace_ids: list[str] | None = None,
) -> dict:
    return {
        "prompt_hash": prompt_hash,
        "dup_count": dup_count,
        "total_cost": total_cost,
        "models": models or ["claude-sonnet-4-20250514"],
        "trace_ids": trace_ids or ["trace-001", "trace-002"],
        "first_seen": "2026-03-01T00:00:00Z",
        "last_seen": "2026-03-01T00:30:00Z",
    }


class TestCacheMissBasics:

    def test_empty_data_returns_empty(self) -> None:
        assert detect_cache_misses([]) == []

    def test_single_occurrence_not_flagged(self) -> None:
        rows = [_prompt_dup_row(dup_count=1)]
        assert detect_cache_misses(rows) == []


class TestCacheMissDetection:

    def test_duplicate_prompts_flagged(self) -> None:
        rows = [_prompt_dup_row(dup_count=5, total_cost=1.00)]
        items = detect_cache_misses(rows)
        assert len(items) == 1
        assert items[0].category == WasteCategory.CACHE_MISSES
        assert items[0].call_count == 5

    def test_savings_accounts_for_cache_discount(self) -> None:
        rows = [_prompt_dup_row(dup_count=10, total_cost=1.00)]
        items = detect_cache_misses(rows)
        assert len(items) == 1
        # cost_per_call = 0.10, cached_cost = 0.01
        # savings = 9 * (0.10 - 0.01) = 0.81
        assert items[0].savings == pytest.approx(0.81, rel=0.01)

    def test_trace_ids_included(self) -> None:
        rows = [_prompt_dup_row(trace_ids=["t1", "t2", "t3"])]
        items = detect_cache_misses(rows)
        assert set(items[0].affected_trace_ids) == {"t1", "t2", "t3"}

    def test_trace_ids_limited_to_20(self) -> None:
        many_traces = [f"t-{i}" for i in range(50)]
        rows = [_prompt_dup_row(trace_ids=many_traces)]
        items = detect_cache_misses(rows)
        assert len(items[0].affected_trace_ids) <= 20

    def test_multiple_rows_sorted_by_savings(self) -> None:
        rows = [
            _prompt_dup_row(prompt_hash="aaa", dup_count=2, total_cost=0.10),
            _prompt_dup_row(prompt_hash="bbb", dup_count=20, total_cost=5.00),
        ]
        items = detect_cache_misses(rows)
        assert len(items) == 2
        assert items[0].savings >= items[1].savings


class TestCacheMissSeverity:

    def test_info_for_small_duplicates(self) -> None:
        rows = [_prompt_dup_row(dup_count=2, total_cost=0.05)]
        items = detect_cache_misses(rows)
        assert items[0].severity == WasteSeverity.INFO

    def test_warning_for_moderate_duplicates(self) -> None:
        rows = [_prompt_dup_row(dup_count=15, total_cost=1.50)]
        items = detect_cache_misses(rows)
        assert items[0].severity == WasteSeverity.WARNING

    def test_critical_for_many_duplicates(self) -> None:
        rows = [_prompt_dup_row(dup_count=50, total_cost=10.0)]
        items = detect_cache_misses(rows)
        assert items[0].severity == WasteSeverity.CRITICAL
