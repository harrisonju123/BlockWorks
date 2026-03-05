"""Tests for the redundant call detector."""

from __future__ import annotations

from blockthrough.waste.detectors.redundant_calls import detect_redundant_calls
from blockthrough.waste.types import WasteCategory, WasteSeverity


def _dup_row(
    trace_id: str = "trace-001",
    tool_name: str = "read_file",
    args_hash: str = "abc123",
    dup_count: int = 3,
    estimated_cost_per_call: float = 0.01,
) -> dict:
    return {
        "trace_id": trace_id,
        "tool_name": tool_name,
        "args_hash": args_hash,
        "dup_count": dup_count,
        "estimated_cost_per_call": estimated_cost_per_call,
    }


class TestRedundantCallsBasics:

    def test_empty_data_returns_empty(self) -> None:
        assert detect_redundant_calls([]) == []

    def test_single_call_not_flagged(self) -> None:
        rows = [_dup_row(dup_count=1)]
        assert detect_redundant_calls(rows) == []


class TestRedundantCallsDetection:

    def test_duplicate_calls_flagged(self) -> None:
        rows = [_dup_row(dup_count=3, estimated_cost_per_call=0.05)]
        items = detect_redundant_calls(rows)
        assert len(items) == 1
        assert items[0].category == WasteCategory.REDUNDANT_CALLS
        assert items[0].call_count == 3

    def test_savings_is_wasted_calls_times_cost(self) -> None:
        rows = [_dup_row(dup_count=5, estimated_cost_per_call=0.10)]
        items = detect_redundant_calls(rows)
        assert len(items) == 1
        # 4 wasted calls * $0.10 = $0.40
        assert items[0].savings == round(4 * 0.10, 6)
        # Projected cost = 1 call = $0.10
        assert items[0].projected_cost == round(0.10, 6)

    def test_trace_id_in_affected(self) -> None:
        rows = [_dup_row(trace_id="trace-xyz")]
        items = detect_redundant_calls(rows)
        assert "trace-xyz" in items[0].affected_trace_ids

    def test_multiple_rows_sorted_by_savings(self) -> None:
        rows = [
            _dup_row(trace_id="a", dup_count=2, estimated_cost_per_call=0.01),
            _dup_row(trace_id="b", dup_count=10, estimated_cost_per_call=0.05),
        ]
        items = detect_redundant_calls(rows)
        assert len(items) == 2
        assert items[0].savings >= items[1].savings


class TestRedundantCallsSeverity:

    def test_info_for_small_dup_count(self) -> None:
        rows = [_dup_row(dup_count=2)]
        items = detect_redundant_calls(rows)
        assert items[0].severity == WasteSeverity.INFO

    def test_warning_for_medium_dup_count(self) -> None:
        rows = [_dup_row(dup_count=5)]
        items = detect_redundant_calls(rows)
        assert items[0].severity == WasteSeverity.WARNING

    def test_critical_for_high_dup_count(self) -> None:
        rows = [_dup_row(dup_count=10)]
        items = detect_redundant_calls(rows)
        assert items[0].severity == WasteSeverity.CRITICAL
