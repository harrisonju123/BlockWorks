"""Tests for trust score calculator — dimension computation and composite scoring."""

from __future__ import annotations

import pytest

from blockthrough.trust.calculator import TrustCalculator, compute_composite
from blockthrough.trust.types import TrustWeights


class TestComputeComposite:

    def test_default_weights_neutral(self) -> None:
        """All dimensions at 0.5 with default weights -> composite ~0.5."""
        weights = TrustWeights()
        result = compute_composite(0.5, 0.5, 0.5, 0.5, weights)
        assert abs(result - 0.5) < 0.01

    def test_all_perfect(self) -> None:
        weights = TrustWeights()
        result = compute_composite(1.0, 1.0, 1.0, 1.0, weights)
        assert abs(result - 1.0) < 0.01

    def test_all_zero(self) -> None:
        weights = TrustWeights()
        result = compute_composite(0.0, 0.0, 0.0, 0.0, weights)
        assert result == 0.0

    def test_clamped_to_unit_interval(self) -> None:
        """Even with unusual weights, output stays in [0, 1]."""
        weights = TrustWeights(
            reliability_weight=0.5,
            efficiency_weight=0.5,
            quality_weight=0.5,
            usage_weight=0.5,
        )
        # Sum of weights > 1, so raw composite could exceed 1
        result = compute_composite(1.0, 1.0, 1.0, 1.0, weights)
        assert result <= 1.0

    def test_single_dimension_dominates(self) -> None:
        """When only reliability has weight, composite equals reliability."""
        weights = TrustWeights(
            reliability_weight=1.0,
            efficiency_weight=0.0,
            quality_weight=0.0,
            usage_weight=0.0,
        )
        result = compute_composite(0.8, 0.2, 0.3, 0.1, weights)
        assert abs(result - 0.8) < 0.001

    def test_weighted_sum_correctness(self) -> None:
        weights = TrustWeights(
            reliability_weight=0.3,
            efficiency_weight=0.25,
            quality_weight=0.3,
            usage_weight=0.15,
        )
        expected = 0.9 * 0.3 + 0.7 * 0.25 + 0.8 * 0.3 + 0.6 * 0.15
        result = compute_composite(0.9, 0.7, 0.8, 0.6, weights)
        assert abs(result - expected) < 0.0001


class TestComputeScore:

    def test_returns_trust_score(self) -> None:
        calc = TrustCalculator()
        score = calc.compute_score("agent-1", 0.8, 0.7, 0.9, 0.5)
        assert score.agent_id == "agent-1"
        assert score.reliability == 0.8
        assert score.efficiency == 0.7
        assert score.quality == 0.9
        assert score.usage_volume == 0.5
        assert 0.0 <= score.composite_score <= 1.0

    def test_composite_matches_compute_composite(self) -> None:
        weights = TrustWeights()
        calc = TrustCalculator(weights)
        score = calc.compute_score("agent-1", 0.8, 0.7, 0.9, 0.5)
        expected = compute_composite(0.8, 0.7, 0.9, 0.5, weights)
        assert abs(score.composite_score - expected) < 0.0001


class TestUpdateReliability:

    def test_perfect_uptime_no_errors(self) -> None:
        calc = TrustCalculator()
        result = calc.update_reliability(uptime_pct=1.0, error_rate=0.0)
        assert result == 1.0

    def test_no_uptime(self) -> None:
        calc = TrustCalculator()
        result = calc.update_reliability(uptime_pct=0.0, error_rate=0.0)
        assert result == 0.0

    def test_high_error_rate_reduces_score(self) -> None:
        calc = TrustCalculator()
        result = calc.update_reliability(uptime_pct=1.0, error_rate=0.5)
        assert abs(result - 0.5) < 0.001

    def test_clamps_inputs(self) -> None:
        calc = TrustCalculator()
        # Out-of-range inputs get clamped
        result = calc.update_reliability(uptime_pct=1.5, error_rate=-0.1)
        assert result == 1.0  # min(1.0, 1.5) * (1.0 - max(0.0, -0.1))


class TestUpdateEfficiency:

    def test_equal_to_benchmark(self) -> None:
        calc = TrustCalculator()
        result = calc.update_efficiency(cost_per_outcome=0.01, benchmark_cost=0.01)
        assert abs(result - 1.0) < 0.001

    def test_cheaper_than_benchmark_capped(self) -> None:
        calc = TrustCalculator()
        result = calc.update_efficiency(cost_per_outcome=0.005, benchmark_cost=0.01)
        assert result == 1.0  # Capped at 1.0

    def test_twice_as_expensive(self) -> None:
        calc = TrustCalculator()
        result = calc.update_efficiency(cost_per_outcome=0.02, benchmark_cost=0.01)
        assert abs(result - 0.5) < 0.001

    def test_zero_benchmark_returns_neutral(self) -> None:
        calc = TrustCalculator()
        result = calc.update_efficiency(cost_per_outcome=0.01, benchmark_cost=0.0)
        assert result == 0.5

    def test_very_expensive_approaches_zero(self) -> None:
        calc = TrustCalculator()
        result = calc.update_efficiency(cost_per_outcome=100.0, benchmark_cost=0.01)
        assert result < 0.01


class TestUpdateQuality:

    def test_single_score(self) -> None:
        calc = TrustCalculator()
        result = calc.update_quality([0.9])
        assert abs(result - 0.9) < 0.001

    def test_multiple_scores_averaged(self) -> None:
        calc = TrustCalculator()
        result = calc.update_quality([0.8, 0.9, 1.0])
        assert abs(result - 0.9) < 0.001

    def test_empty_scores_return_neutral(self) -> None:
        calc = TrustCalculator()
        result = calc.update_quality([])
        assert result == 0.5

    def test_all_perfect(self) -> None:
        calc = TrustCalculator()
        result = calc.update_quality([1.0, 1.0, 1.0])
        assert result == 1.0

    def test_clamped_to_unit(self) -> None:
        calc = TrustCalculator()
        # Scores > 1.0 in the list still produce clamped output
        result = calc.update_quality([1.5, 0.5])
        assert result <= 1.0


class TestUpdateUsage:

    def test_no_calls(self) -> None:
        calc = TrustCalculator()
        result = calc.update_usage(call_count=0, total_agents_count=10)
        assert result == 0.0

    def test_no_agents(self) -> None:
        calc = TrustCalculator()
        result = calc.update_usage(call_count=100, total_agents_count=0)
        assert result == 0.0

    def test_average_usage(self) -> None:
        """One agent with call_count=1 out of total=1 -> ratio=1 -> score=0.5."""
        calc = TrustCalculator()
        result = calc.update_usage(call_count=1, total_agents_count=1)
        assert abs(result - 0.5) < 0.001

    def test_high_usage(self) -> None:
        calc = TrustCalculator()
        result = calc.update_usage(call_count=100, total_agents_count=1)
        assert result > 0.9  # Very high usage -> close to 1.0

    def test_score_in_unit_interval(self) -> None:
        calc = TrustCalculator()
        result = calc.update_usage(call_count=999999, total_agents_count=1)
        assert 0.0 <= result <= 1.0
