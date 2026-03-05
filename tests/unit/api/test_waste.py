"""Tests for the waste score calculation logic."""

from __future__ import annotations

import pytest

from agentproof.api.waste import compute_waste_score
from agentproof.benchmarking.types import FitnessEntry
from agentproof.models import MODEL_CATALOG as MODEL_COST_TIERS
from agentproof.types import TaskType


def _row(
    task_type: str,
    model: str,
    call_count: int = 100,
    total_cost: float = 10.0,
    avg_confidence: float | None = 0.9,
) -> dict:
    """Build a fake query row matching the shape returned by get_waste_analysis."""
    return {
        "task_type": task_type,
        "model": model,
        "call_count": call_count,
        "total_cost": total_cost,
        "avg_confidence": avg_confidence,
    }


def _fitness(
    task_type: str = "classification",
    model: str = "claude-haiku-4-5-20251001",
    avg_quality: float = 0.95,
    sample_size: int = 50,
) -> FitnessEntry:
    return FitnessEntry(
        task_type=task_type,
        model=model,
        avg_quality=avg_quality,
        avg_cost=0.001,
        avg_latency=200.0,
        sample_size=sample_size,
    )


class TestWasteScoreBasics:

    def test_empty_data_returns_zero(self) -> None:
        result = compute_waste_score([])
        assert result.waste_score == 0.0
        assert result.total_potential_savings_usd == 0.0
        assert result.breakdown == []

    def test_score_between_zero_and_one(self) -> None:
        rows = [
            _row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=1000.0),
        ]
        result = compute_waste_score(rows)
        assert 0.0 <= result.waste_score <= 1.0

    def test_no_waste_when_already_optimal(self) -> None:
        for tt in TaskType:
            rows = [_row(tt.value, "gpt-4o-mini", total_cost=5.0)]
            result = compute_waste_score(rows)
            assert result.breakdown == [], (
                f"Tier-3 model should not be flagged for {tt.value}"
            )
            assert result.waste_score == 0.0


class TestTier1OnSimpleTasks:

    @pytest.mark.parametrize(
        "task_type",
        [TaskType.CLASSIFICATION, TaskType.EXTRACTION, TaskType.CONVERSATION],
    )
    def test_tier1_on_simple_task_flagged(self, task_type: TaskType) -> None:
        rows = [_row(task_type.value, "claude-opus-4-20250514", total_cost=20.0)]
        result = compute_waste_score(rows)
        assert len(result.breakdown) == 1
        item = result.breakdown[0]
        assert item.task_type == task_type
        assert item.suggested_model in MODEL_COST_TIERS
        assert MODEL_COST_TIERS[item.suggested_model].tier == 3

    @pytest.mark.parametrize(
        "task_type",
        [TaskType.CLASSIFICATION, TaskType.EXTRACTION, TaskType.CONVERSATION],
    )
    def test_tier2_on_simple_task_flagged(self, task_type: TaskType) -> None:
        rows = [_row(task_type.value, "claude-sonnet-4-20250514", total_cost=10.0)]
        result = compute_waste_score(rows)
        assert len(result.breakdown) == 1
        assert MODEL_COST_TIERS[result.breakdown[0].suggested_model].tier == 3


class TestSummarization:

    def test_tier1_on_summarization_flagged(self) -> None:
        rows = [_row(TaskType.SUMMARIZATION.value, "claude-opus-4-20250514", total_cost=15.0)]
        result = compute_waste_score(rows)
        assert len(result.breakdown) == 1
        assert MODEL_COST_TIERS[result.breakdown[0].suggested_model].tier == 2

    def test_tier2_on_summarization_not_flagged(self) -> None:
        rows = [_row(TaskType.SUMMARIZATION.value, "claude-sonnet-4-20250514", total_cost=8.0)]
        result = compute_waste_score(rows)
        assert result.breakdown == []


class TestCodeGenAndReasoning:

    @pytest.mark.parametrize(
        "task_type", [TaskType.CODE_GENERATION, TaskType.REASONING]
    )
    def test_tier1_flagged(self, task_type: TaskType) -> None:
        rows = [_row(task_type.value, "claude-opus-4-20250514", total_cost=25.0)]
        result = compute_waste_score(rows)
        assert len(result.breakdown) == 1
        assert MODEL_COST_TIERS[result.breakdown[0].suggested_model].tier == 2

    @pytest.mark.parametrize(
        "task_type", [TaskType.CODE_GENERATION, TaskType.REASONING]
    )
    def test_tier2_not_flagged(self, task_type: TaskType) -> None:
        rows = [_row(task_type.value, "claude-sonnet-4-20250514", total_cost=12.0)]
        result = compute_waste_score(rows)
        assert result.breakdown == []


class TestToolSelection:

    def test_tier1_flagged(self) -> None:
        rows = [_row(TaskType.TOOL_SELECTION.value, "claude-opus-4-20250514", total_cost=5.0)]
        result = compute_waste_score(rows)
        assert len(result.breakdown) == 1
        assert MODEL_COST_TIERS[result.breakdown[0].suggested_model].tier == 3


class TestSavingsCalculation:

    def test_savings_equals_current_minus_projected(self) -> None:
        rows = [
            _row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=100.0),
        ]
        result = compute_waste_score(rows)
        item = result.breakdown[0]
        assert item.current_cost_usd == pytest.approx(100.0)
        assert item.savings_usd == pytest.approx(item.current_cost_usd - item.projected_cost_usd)
        assert item.projected_cost_usd < item.current_cost_usd

    def test_waste_score_is_savings_over_spend(self) -> None:
        rows = [
            _row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=60.0),
            _row(TaskType.CLASSIFICATION.value, "gpt-4o-mini", total_cost=40.0),
        ]
        result = compute_waste_score(rows)
        total_spend = 60.0 + 40.0
        total_savings = result.total_potential_savings_usd
        expected_score = total_savings / total_spend
        assert result.waste_score == pytest.approx(expected_score, abs=1e-5)

    def test_projected_cost_uses_cost_ratio(self) -> None:
        current_model = "claude-opus-4-20250514"
        rows = [
            _row(TaskType.CLASSIFICATION.value, current_model, total_cost=100.0),
        ]
        result = compute_waste_score(rows)
        item = result.breakdown[0]

        cur = MODEL_COST_TIERS[current_model]
        sug = MODEL_COST_TIERS[item.suggested_model]
        expected_projected = 100.0 * (sug.avg_cost / cur.avg_cost)

        assert item.projected_cost_usd == pytest.approx(expected_projected, rel=1e-4)


class TestUnknownModelsAndTaskTypes:

    def test_unknown_model_not_flagged(self) -> None:
        rows = [_row(TaskType.CLASSIFICATION.value, "some-custom-model", total_cost=50.0)]
        result = compute_waste_score(rows)
        assert result.breakdown == []

    def test_unknown_task_type_not_flagged(self) -> None:
        rows = [_row(TaskType.UNKNOWN.value, "claude-opus-4-20250514", total_cost=50.0)]
        result = compute_waste_score(rows)
        assert result.breakdown == []

    def test_confidence_defaults_to_half_when_no_fitness(self) -> None:
        """Without fitness data, heuristic confidence is capped at 0.5."""
        rows = [
            _row(
                TaskType.CLASSIFICATION.value,
                "claude-opus-4-20250514",
                total_cost=10.0,
                avg_confidence=None,
            ),
        ]
        result = compute_waste_score(rows)
        assert result.breakdown[0].confidence == 0.5


class TestBreakdownSorting:

    def test_sorted_by_savings_desc(self) -> None:
        rows = [
            _row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=10.0),
            _row(TaskType.EXTRACTION.value, "claude-opus-4-20250514", total_cost=50.0),
            _row(TaskType.CONVERSATION.value, "claude-opus-4-20250514", total_cost=30.0),
        ]
        result = compute_waste_score(rows)
        savings_values = [item.savings_usd for item in result.breakdown]
        assert savings_values == sorted(savings_values, reverse=True)


class TestFitnessAwarePath:
    """Tests for compute_waste_score with fitness_entries provided."""

    def test_backward_compatible_without_fitness(self) -> None:
        """compute_waste_score(rows) still works — no fitness = heuristic fallback."""
        rows = [_row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=100.0)]
        result = compute_waste_score(rows)
        assert len(result.breakdown) == 1
        assert result.breakdown[0].suggestion_source == "heuristic"
        assert result.breakdown[0].confidence == 0.5

    def test_uses_fitness_data_when_provided(self) -> None:
        rows = [_row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=100.0)]
        fitness = [_fitness(task_type="classification", avg_quality=0.95, sample_size=40)]
        result = compute_waste_score(rows, fitness)
        assert len(result.breakdown) == 1
        item = result.breakdown[0]
        assert item.suggestion_source == "fitness"
        assert item.quality_score == pytest.approx(0.95)
        assert item.sample_size == 40
        assert item.confidence > 0.5

    def test_fitness_gives_higher_confidence_than_heuristic(self) -> None:
        rows = [_row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=100.0)]

        heuristic_result = compute_waste_score(rows)
        fitness_result = compute_waste_score(
            rows, [_fitness(avg_quality=0.99, sample_size=100)],
        )

        h_conf = heuristic_result.breakdown[0].confidence
        f_conf = fitness_result.breakdown[0].confidence
        assert f_conf > h_conf

    def test_new_fields_default_to_none_without_fitness(self) -> None:
        rows = [_row(TaskType.CLASSIFICATION.value, "claude-opus-4-20250514", total_cost=100.0)]
        result = compute_waste_score(rows)
        item = result.breakdown[0]
        assert item.suggestion_source == "heuristic"
        assert item.quality_score is None
        assert item.sample_size == 0
