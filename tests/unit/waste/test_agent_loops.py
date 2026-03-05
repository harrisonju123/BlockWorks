"""Tests for the agent loop detector."""

from __future__ import annotations

from blockthrough.waste.detectors.agent_loops import detect_agent_loops
from blockthrough.waste.types import WasteCategory, WasteSeverity


def _pattern_row(
    trace_id: str = "trace-001",
    tool_name: str = "edit_file",
    args_hashes: list[str] | None = None,
    call_count: int = 5,
    total_cost: float = 0.50,
    estimated_cost_per_call: float = 0.10,
) -> dict:
    return {
        "trace_id": trace_id,
        "tool_name": tool_name,
        "args_hashes": args_hashes or ["h1", "h1", "h1", "h1", "h1"],
        "call_count": call_count,
        "total_cost": total_cost,
        "estimated_cost_per_call": estimated_cost_per_call,
    }


class TestAgentLoopsBasics:

    def test_empty_data_returns_empty(self) -> None:
        assert detect_agent_loops([]) == []

    def test_few_calls_not_flagged(self) -> None:
        rows = [_pattern_row(call_count=2, args_hashes=["h1", "h1"])]
        assert detect_agent_loops(rows) == []


class TestAgentLoopsDetection:

    def test_identical_hashes_flagged(self) -> None:
        rows = [_pattern_row(
            call_count=5,
            args_hashes=["same", "same", "same", "same", "same"],
            total_cost=0.50,
        )]
        items = detect_agent_loops(rows)
        assert len(items) == 1
        assert items[0].category == WasteCategory.AGENT_LOOPS
        assert items[0].call_count == 5

    def test_different_hashes_not_flagged(self) -> None:
        """Completely different hashes shouldn't form a loop."""
        rows = [_pattern_row(
            call_count=5,
            args_hashes=["aaa", "bbb", "ccc", "ddd", "eee"],
            total_cost=0.50,
        )]
        items = detect_agent_loops(rows)
        # Each hash is unique, no consecutive run >= 3
        assert items == []

    def test_partial_loop_detected(self) -> None:
        """A run of identical hashes in the middle of varied calls."""
        rows = [_pattern_row(
            call_count=7,
            args_hashes=["x", "y", "same", "same", "same", "same", "z"],
            total_cost=0.70,
        )]
        items = detect_agent_loops(rows)
        assert len(items) == 1

    def test_savings_excludes_productive_calls(self) -> None:
        rows = [_pattern_row(
            call_count=5,
            args_hashes=["h", "h", "h", "h", "h"],
            total_cost=1.00,
        )]
        items = detect_agent_loops(rows)
        assert len(items) == 1
        # 5 calls, 2 productive, 3 wasted. cost_per_call = 0.20
        # savings = 3 * 0.20 = 0.60
        assert items[0].savings > 0
        assert items[0].projected_cost < items[0].current_cost

    def test_trace_id_in_affected(self) -> None:
        rows = [_pattern_row(trace_id="trace-loop-1")]
        items = detect_agent_loops(rows)
        assert "trace-loop-1" in items[0].affected_trace_ids


class TestAgentLoopsSeverity:

    def test_info_for_short_loop(self) -> None:
        rows = [_pattern_row(
            call_count=3,
            args_hashes=["h", "h", "h"],
            total_cost=0.30,
        )]
        items = detect_agent_loops(rows)
        assert items[0].severity == WasteSeverity.INFO

    def test_warning_for_medium_loop(self) -> None:
        hashes = ["h"] * 6
        rows = [_pattern_row(call_count=6, args_hashes=hashes, total_cost=0.60)]
        items = detect_agent_loops(rows)
        assert items[0].severity == WasteSeverity.WARNING

    def test_critical_for_long_loop(self) -> None:
        hashes = ["h"] * 12
        rows = [_pattern_row(call_count=12, args_hashes=hashes, total_cost=1.20)]
        items = detect_agent_loops(rows)
        assert items[0].severity == WasteSeverity.CRITICAL


class TestAgentLoopsCustomThresholds:

    def test_custom_min_iterations(self) -> None:
        rows = [_pattern_row(
            call_count=3,
            args_hashes=["h", "h", "h"],
            total_cost=0.30,
        )]
        # Default min_iterations=3 should flag
        assert len(detect_agent_loops(rows)) == 1
        # Higher threshold should not flag
        assert len(detect_agent_loops(rows, min_iterations=5)) == 0
