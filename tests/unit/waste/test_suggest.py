"""Tests for the unified suggestion engine."""

from __future__ import annotations

import pytest

from agentproof.benchmarking.types import FitnessEntry
from agentproof.models import MODEL_CATALOG
from agentproof.waste.suggest import suggest_alternative


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


class TestFitnessPath:

    def test_picks_cheapest_above_threshold(self) -> None:
        entries = [
            _fitness(model="claude-haiku-4-5-20251001", avg_quality=0.92),
            _fitness(model="gpt-4o-mini", avg_quality=0.90),
        ]
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", entries, quality_threshold=0.85,
        )
        assert result is not None
        assert result.source == "fitness"
        # gpt-4o-mini is cheaper than haiku
        assert MODEL_CATALOG[result.suggested_model].avg_cost <= MODEL_CATALOG["claude-haiku-4-5-20251001"].avg_cost

    def test_returns_none_when_no_model_meets_threshold(self) -> None:
        entries = [
            _fitness(model="claude-haiku-4-5-20251001", avg_quality=0.60),
            _fitness(model="gpt-4o-mini", avg_quality=0.50),
        ]
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", entries, quality_threshold=0.85,
        )
        # No fitness match, should fall back to heuristic
        assert result is not None
        assert result.source == "heuristic"

    def test_fitness_quality_and_sample_size_in_result(self) -> None:
        entries = [_fitness(avg_quality=0.93, sample_size=30)]
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", entries, quality_threshold=0.85,
        )
        assert result is not None
        assert result.quality == pytest.approx(0.93)
        assert result.sample_size == 30
        assert result.source == "fitness"

    def test_skips_models_not_cheaper(self) -> None:
        # Haiku is same tier but if we're already using a tier-3 model
        entries = [_fitness(model="claude-opus-4-20250514", avg_quality=0.99)]
        result = suggest_alternative(
            "classification", "claude-haiku-4-5-20251001", entries, quality_threshold=0.85,
        )
        # Opus is more expensive than Haiku — won't be suggested via fitness path
        # Should fall back to heuristic, which also won't flag tier-3
        assert result is None


class TestConfidence:

    def test_high_quality_high_samples_gives_high_confidence(self) -> None:
        entries = [_fitness(avg_quality=0.99, sample_size=100)]
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", entries, quality_threshold=0.85,
        )
        assert result is not None
        assert result.confidence > 0.8

    def test_low_samples_gives_low_confidence(self) -> None:
        entries = [_fitness(avg_quality=0.95, sample_size=2)]
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", entries, quality_threshold=0.85,
        )
        assert result is not None
        assert result.confidence < 0.5

    def test_just_above_threshold_gives_lower_confidence(self) -> None:
        entries = [_fitness(avg_quality=0.86, sample_size=50)]
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", entries, quality_threshold=0.85,
        )
        assert result is not None
        # Barely above threshold → low quality_factor
        high_quality_entries = [_fitness(avg_quality=0.99, sample_size=50)]
        high_result = suggest_alternative(
            "classification", "claude-opus-4-20250514", high_quality_entries, quality_threshold=0.85,
        )
        assert high_result is not None
        assert result.confidence < high_result.confidence

    def test_confidence_scales_with_sample_size(self) -> None:
        small = suggest_alternative(
            "classification", "claude-opus-4-20250514",
            [_fitness(avg_quality=0.95, sample_size=5)], quality_threshold=0.85,
        )
        large = suggest_alternative(
            "classification", "claude-opus-4-20250514",
            [_fitness(avg_quality=0.95, sample_size=50)], quality_threshold=0.85,
        )
        assert small is not None and large is not None
        assert large.confidence > small.confidence


class TestHeuristicFallback:

    def test_falls_back_when_fitness_entries_empty(self) -> None:
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", fitness_entries=[],
        )
        assert result is not None
        assert result.source == "heuristic"
        assert result.quality is None
        assert result.sample_size == 0

    def test_falls_back_when_fitness_entries_none(self) -> None:
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", fitness_entries=None,
        )
        assert result is not None
        assert result.source == "heuristic"

    def test_heuristic_confidence_capped_at_half(self) -> None:
        result = suggest_alternative(
            "classification", "claude-opus-4-20250514", fitness_entries=None,
        )
        assert result is not None
        assert result.confidence == 0.5

    def test_heuristic_tier1_simple_suggests_tier3(self) -> None:
        for tt in ("classification", "extraction", "conversation"):
            result = suggest_alternative(tt, "claude-opus-4-20250514")
            assert result is not None
            assert MODEL_CATALOG[result.suggested_model].tier == 3

    def test_heuristic_tier1_code_gen_suggests_tier2(self) -> None:
        result = suggest_alternative("code_generation", "claude-opus-4-20250514")
        assert result is not None
        assert MODEL_CATALOG[result.suggested_model].tier == 2

    def test_heuristic_tier3_not_flagged(self) -> None:
        result = suggest_alternative("classification", "gpt-4o-mini")
        assert result is None

    def test_unknown_model_returns_none(self) -> None:
        result = suggest_alternative("classification", "nonexistent-model-xyz")
        assert result is None

    def test_unknown_task_type_returns_none_on_heuristic(self) -> None:
        result = suggest_alternative("unknown", "claude-opus-4-20250514")
        assert result is None

    def test_cost_ratio_populated(self) -> None:
        result = suggest_alternative("classification", "claude-opus-4-20250514")
        assert result is not None
        assert 0 < result.cost_ratio < 1
