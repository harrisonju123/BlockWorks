"""Tests for model comparison logic.

Validates quality/cost/latency deltas, recommendation text generation,
and error handling for missing models.
"""

from __future__ import annotations

import pytest

from agentproof.fitness.comparison import ComparisonError, compare_models
from agentproof.fitness.types import LeaderboardEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    model: str = "model-a",
    task_type: str = "code_generation",
    quality_score: float = 0.8,
    cost_per_1k: float = 5.0,
    latency_ms: float = 500.0,
    rank: int = 1,
) -> LeaderboardEntry:
    return LeaderboardEntry(
        model=model,
        task_type=task_type,
        quality_score=quality_score,
        cost_per_1k=cost_per_1k,
        latency_ms=latency_ms,
        sample_size=100,
        rank=rank,
    )


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------


class TestDeltas:

    def test_quality_delta_positive_when_a_better(self) -> None:
        board = [
            _entry(model="a", quality_score=0.9),
            _entry(model="b", quality_score=0.7),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert result.quality_delta == pytest.approx(0.2)

    def test_quality_delta_negative_when_b_better(self) -> None:
        board = [
            _entry(model="a", quality_score=0.6),
            _entry(model="b", quality_score=0.8),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert result.quality_delta == pytest.approx(-0.2)

    def test_cost_delta(self) -> None:
        board = [
            _entry(model="a", cost_per_1k=3.0),
            _entry(model="b", cost_per_1k=5.0),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert result.cost_delta == pytest.approx(-2.0)

    def test_latency_delta(self) -> None:
        board = [
            _entry(model="a", latency_ms=200.0),
            _entry(model="b", latency_ms=500.0),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert result.latency_delta == pytest.approx(-300.0)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:

    def test_a_better_quality_higher_cost(self) -> None:
        board = [
            _entry(model="a", quality_score=0.9, cost_per_1k=10.0),
            _entry(model="b", quality_score=0.7, cost_per_1k=5.0),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert "a" in result.recommendation
        assert "better quality" in result.recommendation
        assert "higher cost" in result.recommendation

    def test_a_better_quality_lower_cost(self) -> None:
        board = [
            _entry(model="a", quality_score=0.9, cost_per_1k=3.0),
            _entry(model="b", quality_score=0.7, cost_per_1k=5.0),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert "a" in result.recommendation
        assert "better quality" in result.recommendation
        assert "lower cost" in result.recommendation

    def test_comparable_quality_cheaper_model_recommended(self) -> None:
        """< 2% quality difference -> recommend the cheaper one."""
        board = [
            _entry(model="a", quality_score=0.80, cost_per_1k=3.0),
            _entry(model="b", quality_score=0.81, cost_per_1k=5.0),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert "comparable quality" in result.recommendation
        assert "a" in result.recommendation
        assert "lower cost" in result.recommendation

    def test_identical_models(self) -> None:
        """Same quality, same cost -> equivalent."""
        board = [
            _entry(model="a", quality_score=0.80, cost_per_1k=5.0),
            _entry(model="b", quality_score=0.80, cost_per_1k=5.0),
        ]
        result = compare_models("a", "b", "code_generation", board)

        assert "equivalent" in result.recommendation


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrors:

    def test_model_a_not_found(self) -> None:
        board = [_entry(model="b")]

        with pytest.raises(ComparisonError, match="missing"):
            compare_models("missing", "b", "code_generation", board)

    def test_model_b_not_found(self) -> None:
        board = [_entry(model="a")]

        with pytest.raises(ComparisonError, match="missing"):
            compare_models("a", "missing", "code_generation", board)

    def test_wrong_task_type(self) -> None:
        board = [
            _entry(model="a", task_type="summarization"),
            _entry(model="b", task_type="summarization"),
        ]

        with pytest.raises(ComparisonError):
            compare_models("a", "b", "code_generation", board)
