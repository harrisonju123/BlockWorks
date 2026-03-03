"""Tests for the model overkill detector."""

from __future__ import annotations

import pytest

from agentproof.benchmarking.types import FitnessEntry
from agentproof.waste.detectors.model_overkill import detect_model_overkill
from agentproof.waste.types import WasteCategory, WasteSeverity


def _usage_row(
    task_type: str = "classification",
    model: str = "claude-opus-4-20250514",
    call_count: int = 100,
    total_cost: float = 50.0,
) -> dict:
    return {
        "task_type": task_type,
        "model": model,
        "call_count": call_count,
        "total_cost": total_cost,
    }


def _fitness(
    task_type: str = "classification",
    model: str = "claude-haiku-4-5-20251001",
    avg_quality: float = 0.95,
    avg_cost: float = 0.001,
    avg_latency: float = 200.0,
    sample_size: int = 50,
) -> FitnessEntry:
    return FitnessEntry(
        task_type=task_type,
        model=model,
        avg_quality=avg_quality,
        avg_cost=avg_cost,
        avg_latency=avg_latency,
        sample_size=sample_size,
    )


class TestModelOverkillBasics:

    def test_empty_data_returns_empty(self) -> None:
        assert detect_model_overkill([], []) == []

    def test_empty_usage_returns_empty(self) -> None:
        fitness = [_fitness()]
        assert detect_model_overkill([], fitness) == []

    def test_empty_fitness_returns_empty(self) -> None:
        usage = [_usage_row()]
        assert detect_model_overkill(usage, []) == []


class TestModelOverkillDetection:

    def test_opus_on_classification_flagged_when_haiku_qualifies(self) -> None:
        usage = [_usage_row(task_type="classification", model="claude-opus-4-20250514", total_cost=100.0)]
        fitness = [_fitness(task_type="classification", model="claude-haiku-4-5-20251001", avg_quality=0.95)]

        items = detect_model_overkill(usage, fitness)
        assert len(items) == 1
        assert items[0].category == WasteCategory.MODEL_OVERKILL
        assert items[0].savings > 0
        assert "claude-haiku-4-5-20251001" in items[0].description

    def test_not_flagged_when_cheaper_model_below_quality_threshold(self) -> None:
        usage = [_usage_row(task_type="code_generation", model="claude-opus-4-20250514")]
        fitness = [_fitness(task_type="code_generation", model="claude-haiku-4-5-20251001", avg_quality=0.70)]

        items = detect_model_overkill(usage, fitness, quality_threshold=0.90)
        assert items == []

    def test_not_flagged_when_already_cheapest(self) -> None:
        usage = [_usage_row(model="gpt-4o-mini", total_cost=5.0)]
        fitness = [_fitness(model="gpt-4o-mini", avg_quality=0.92)]

        items = detect_model_overkill(usage, fitness)
        assert items == []

    def test_unknown_model_skipped(self) -> None:
        usage = [_usage_row(model="unknown-model-123")]
        fitness = [_fitness()]
        assert detect_model_overkill(usage, fitness) == []


class TestModelOverkillSavings:

    def test_savings_positive_and_less_than_current_cost(self) -> None:
        usage = [_usage_row(total_cost=200.0)]
        fitness = [_fitness(avg_quality=0.95)]

        items = detect_model_overkill(usage, fitness)
        assert len(items) == 1
        assert items[0].savings > 0
        assert items[0].savings < 200.0
        assert items[0].current_cost == pytest.approx(200.0)
        assert items[0].projected_cost < items[0].current_cost

    def test_sorted_by_savings_descending(self) -> None:
        usage = [
            _usage_row(task_type="classification", total_cost=10.0),
            _usage_row(task_type="extraction", total_cost=100.0),
        ]
        fitness = [
            _fitness(task_type="classification", avg_quality=0.95),
            _fitness(task_type="extraction", avg_quality=0.92),
        ]

        items = detect_model_overkill(usage, fitness)
        assert len(items) == 2
        assert items[0].savings >= items[1].savings


class TestModelOverkillSeverity:

    def test_critical_severity_for_large_savings(self) -> None:
        usage = [_usage_row(total_cost=5000.0)]
        fitness = [_fitness(avg_quality=0.95)]

        items = detect_model_overkill(usage, fitness)
        assert items[0].severity == WasteSeverity.CRITICAL

    def test_info_severity_for_small_savings(self) -> None:
        usage = [_usage_row(total_cost=2.0)]
        fitness = [_fitness(avg_quality=0.95)]

        items = detect_model_overkill(usage, fitness)
        if items:
            assert items[0].severity == WasteSeverity.INFO
