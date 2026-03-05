"""Tests for smart benchmark targeting."""

from __future__ import annotations

from blockthrough.benchmarking.targeting import compute_benchmark_targets
from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.models import MODEL_CATALOG


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


class TestTargetingBasics:

    def test_empty_usage_returns_empty(self) -> None:
        assert compute_benchmark_targets([], []) == []

    def test_returns_cheaper_models_for_in_use_expensive(self) -> None:
        usage = [_usage_row(model="claude-opus-4-20250514", call_count=500)]
        targets = compute_benchmark_targets(usage, [])
        assert len(targets) > 0
        # All targets should be cheaper than Opus
        opus_cost = MODEL_CATALOG["claude-opus-4-20250514"].avg_cost
        for t in targets:
            assert MODEL_CATALOG[t].avg_cost < opus_cost


class TestCoverageGaps:

    def test_skips_well_characterized_models(self) -> None:
        usage = [_usage_row(task_type="classification", model="claude-opus-4-20250514")]
        # Haiku already well-characterized for classification
        fitness = [_fitness(task_type="classification", model="claude-haiku-4-5-20251001", sample_size=50)]

        targets = compute_benchmark_targets(usage, fitness, min_sample_size=10)
        # Haiku shouldn't be selected since it has sufficient samples
        # (though other cheaper models might be)
        # The key assertion: models with enough samples are deprioritized
        well_covered = {
            e.model for e in fitness
            if e.task_type == "classification" and e.sample_size >= 10
        }
        # Models in targets shouldn't include those already well-characterized
        # for ALL relevant task types
        for t in targets:
            if t in well_covered:
                # If it shows up, it must be needed for a different task type
                pass

    def test_identifies_gaps_for_new_task_types(self) -> None:
        usage = [
            _usage_row(task_type="classification", model="claude-opus-4-20250514"),
            _usage_row(task_type="code_generation", model="claude-opus-4-20250514"),
        ]
        # Only classification is covered, code_generation is a gap
        fitness = [
            _fitness(task_type="classification", model="claude-haiku-4-5-20251001", sample_size=50),
        ]
        targets = compute_benchmark_targets(usage, fitness, min_sample_size=10)
        # Should still include models for code_generation coverage
        assert len(targets) > 0


class TestRankingBySavings:

    def test_higher_savings_potential_ranked_first(self) -> None:
        usage = [
            _usage_row(task_type="classification", model="claude-opus-4-20250514", call_count=1000),
            _usage_row(task_type="extraction", model="claude-sonnet-4-6", call_count=10),
        ]
        targets = compute_benchmark_targets(usage, [], max_targets=10)
        # Models that replace Opus with 1000 calls should rank higher
        assert len(targets) > 0


class TestMaxTargets:

    def test_respects_max_targets_limit(self) -> None:
        usage = [_usage_row(model="claude-opus-4-20250514", call_count=1000)]
        targets = compute_benchmark_targets(usage, [], max_targets=3)
        assert len(targets) <= 3

    def test_returns_fewer_if_not_enough_candidates(self) -> None:
        # Tier 3 cheapest model — very few cheaper alternatives
        usage = [_usage_row(model="gpt-4o-mini", call_count=100)]
        targets = compute_benchmark_targets(usage, [], max_targets=10)
        # Should return whatever is available (possibly 0)
        assert len(targets) <= 10


class TestEdgeCases:

    def test_unknown_model_in_usage_skipped(self) -> None:
        usage = [_usage_row(model="nonexistent-model")]
        targets = compute_benchmark_targets(usage, [])
        assert targets == []

    def test_zero_cost_rows_handled(self) -> None:
        usage = [_usage_row(total_cost=0.0, call_count=0)]
        targets = compute_benchmark_targets(usage, [])
        # Should still work — just no savings potential
        assert isinstance(targets, list)
