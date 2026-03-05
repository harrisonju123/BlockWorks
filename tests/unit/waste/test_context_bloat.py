"""Tests for the context bloat analyzer."""

from __future__ import annotations

import pytest

from blockthrough.waste.detectors.context_bloat import detect_context_bloat
from blockthrough.waste.types import WasteCategory, WasteSeverity


def _event(
    trace_id: str = "trace-001",
    model: str = "claude-sonnet-4-20250514",
    prompt_tokens: int = 3000,
    completion_tokens: int = 50,
    system_prompt_tokens: int = 2500,
    estimated_cost: float = 0.02,
) -> dict:
    return {
        "trace_id": trace_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "system_prompt_tokens": system_prompt_tokens,
        "estimated_cost": estimated_cost,
    }


class TestContextBloatBasics:

    def test_empty_data_returns_empty(self) -> None:
        assert detect_context_bloat([]) == []

    def test_no_bloat_when_system_prompt_small(self) -> None:
        events = [_event(system_prompt_tokens=500)]
        assert detect_context_bloat(events) == []

    def test_no_bloat_when_completion_large(self) -> None:
        events = [_event(system_prompt_tokens=3000, completion_tokens=500)]
        assert detect_context_bloat(events) == []


class TestContextBloatDetection:

    def test_bloat_flagged_when_high_ratio(self) -> None:
        events = [_event(system_prompt_tokens=3000, completion_tokens=50)]
        items = detect_context_bloat(events)
        assert len(items) == 1
        assert items[0].category == WasteCategory.CONTEXT_BLOAT

    def test_savings_positive(self) -> None:
        events = [_event(system_prompt_tokens=5000, completion_tokens=30, estimated_cost=0.10)]
        items = detect_context_bloat(events)
        assert len(items) == 1
        assert items[0].savings > 0
        assert items[0].projected_cost < items[0].current_cost

    def test_grouped_by_trace(self) -> None:
        events = [
            _event(trace_id="trace-a", system_prompt_tokens=2500, completion_tokens=40),
            _event(trace_id="trace-a", system_prompt_tokens=2800, completion_tokens=60),
            _event(trace_id="trace-b", system_prompt_tokens=3000, completion_tokens=20),
        ]
        items = detect_context_bloat(events)
        trace_ids = {item.affected_trace_ids[0] for item in items}
        assert "trace-a" in trace_ids
        assert "trace-b" in trace_ids

    def test_custom_thresholds(self) -> None:
        events = [_event(system_prompt_tokens=1500, completion_tokens=80)]
        # Default threshold (2000) should not flag
        assert detect_context_bloat(events) == []
        # Lower threshold should flag
        items = detect_context_bloat(events, system_prompt_min=1000)
        assert len(items) == 1


class TestContextBloatSeverity:

    def test_info_for_small_bloat(self) -> None:
        events = [_event(system_prompt_tokens=2100, completion_tokens=80)]
        items = detect_context_bloat(events)
        if items:
            assert items[0].severity == WasteSeverity.INFO

    def test_warning_for_moderate_bloat(self) -> None:
        events = [_event(system_prompt_tokens=6000, completion_tokens=30, estimated_cost=0.05)]
        items = detect_context_bloat(events)
        assert items[0].severity == WasteSeverity.WARNING

    def test_critical_for_severe_bloat(self) -> None:
        events = [
            _event(trace_id="t", system_prompt_tokens=1200, completion_tokens=10, estimated_cost=0.01)
            for _ in range(15)
        ]
        items = detect_context_bloat(events, system_prompt_min=1000)
        assert len(items) == 1
        assert items[0].severity == WasteSeverity.CRITICAL
