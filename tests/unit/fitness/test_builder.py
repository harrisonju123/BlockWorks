"""Tests for the leaderboard builder.

Validates ranking logic, sample size filtering, verification marking,
tie-breaking, and edge cases (empty data, single model, ties).
"""

from __future__ import annotations

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.fitness.builder import build_leaderboard
from blockthrough.fitness.types import FitnessIndexConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    model: str = "model-a",
    task_type: str = "code_generation",
    avg_quality: float = 0.8,
    avg_cost: float = 0.001,
    avg_latency: float = 500.0,
    sample_size: int = 100,
) -> FitnessEntry:
    return FitnessEntry(
        model=model,
        task_type=task_type,
        avg_quality=avg_quality,
        avg_cost=avg_cost,
        avg_latency=avg_latency,
        sample_size=sample_size,
    )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class TestRanking:

    def test_ranks_by_quality_descending(self) -> None:
        entries = [
            _entry(model="low", avg_quality=0.6),
            _entry(model="high", avg_quality=0.9),
            _entry(model="mid", avg_quality=0.75),
        ]
        board = build_leaderboard(entries)

        assert board[0].model == "high"
        assert board[0].rank == 1
        assert board[1].model == "mid"
        assert board[1].rank == 2
        assert board[2].model == "low"
        assert board[2].rank == 3

    def test_cost_tiebreaker_when_quality_equal(self) -> None:
        entries = [
            _entry(model="expensive", avg_quality=0.8, avg_cost=0.01),
            _entry(model="cheap", avg_quality=0.8, avg_cost=0.001),
        ]
        board = build_leaderboard(entries)

        assert board[0].model == "cheap"
        assert board[0].rank == 1
        assert board[1].model == "expensive"
        assert board[1].rank == 2

    def test_ranks_are_per_task_type(self) -> None:
        """Each task type should have independent rank numbering."""
        entries = [
            _entry(model="a", task_type="code_generation", avg_quality=0.9),
            _entry(model="b", task_type="code_generation", avg_quality=0.8),
            _entry(model="a", task_type="summarization", avg_quality=0.7),
            _entry(model="b", task_type="summarization", avg_quality=0.85),
        ]
        board = build_leaderboard(entries)

        code_entries = [e for e in board if e.task_type == "code_generation"]
        summ_entries = [e for e in board if e.task_type == "summarization"]

        assert code_entries[0].model == "a"
        assert code_entries[0].rank == 1
        assert summ_entries[0].model == "b"
        assert summ_entries[0].rank == 1

    def test_cost_per_1k_is_scaled(self) -> None:
        """cost_per_1k should be avg_cost * 1000."""
        entries = [_entry(avg_cost=0.005)]
        board = build_leaderboard(entries)

        assert board[0].cost_per_1k == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFiltering:

    def test_filters_below_min_sample_size(self) -> None:
        entries = [
            _entry(model="big", sample_size=100),
            _entry(model="small", sample_size=5),
        ]
        board = build_leaderboard(entries)

        models = [e.model for e in board]
        assert "big" in models
        assert "small" not in models

    def test_custom_min_sample_size(self) -> None:
        config = FitnessIndexConfig(min_sample_size=50)
        entries = [
            _entry(model="enough", sample_size=50),
            _entry(model="not-enough", sample_size=49),
        ]
        board = build_leaderboard(entries, config=config)

        assert len(board) == 1
        assert board[0].model == "enough"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class TestVerification:

    def test_verified_when_in_set(self) -> None:
        entries = [_entry(model="verified-model", task_type="code_generation")]
        verified = {("verified-model", "code_generation")}

        board = build_leaderboard(entries, verified_tasks=verified)
        assert board[0].verified is True

    def test_not_verified_when_absent(self) -> None:
        entries = [_entry(model="unverified", task_type="code_generation")]
        verified = {("other-model", "code_generation")}

        board = build_leaderboard(entries, verified_tasks=verified)
        assert board[0].verified is False

    def test_no_verification_data_means_all_unverified(self) -> None:
        entries = [_entry()]
        board = build_leaderboard(entries, verified_tasks=None)
        assert board[0].verified is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_empty_entries(self) -> None:
        board = build_leaderboard([])
        assert board == []

    def test_single_model(self) -> None:
        entries = [_entry(model="only")]
        board = build_leaderboard(entries)

        assert len(board) == 1
        assert board[0].rank == 1

    def test_all_filtered_out(self) -> None:
        """All entries below min_sample_size -> empty board."""
        entries = [_entry(sample_size=1), _entry(model="b", sample_size=2)]
        board = build_leaderboard(entries)

        assert board == []
