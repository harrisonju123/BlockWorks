"""Tests for badge and widget data generation.

Validates badge payloads, summary widget structure, and edge cases
like missing models and empty leaderboards.
"""

from __future__ import annotations

from blockthrough.fitness.types import LeaderboardEntry
from blockthrough.fitness.widget import generate_badge_data, generate_summary_widget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    model: str = "model-a",
    task_type: str = "code_generation",
    quality_score: float = 0.85,
    cost_per_1k: float = 5.0,
    latency_ms: float = 400.0,
    rank: int = 1,
    verified: bool = False,
    sample_size: int = 100,
) -> LeaderboardEntry:
    return LeaderboardEntry(
        model=model,
        task_type=task_type,
        quality_score=quality_score,
        cost_per_1k=cost_per_1k,
        latency_ms=latency_ms,
        sample_size=sample_size,
        rank=rank,
        verified=verified,
    )


# ---------------------------------------------------------------------------
# generate_badge_data
# ---------------------------------------------------------------------------


class TestBadgeData:

    def test_found_model_returns_ranked(self) -> None:
        board = [_entry(model="gpt-4o", task_type="code_generation", rank=2)]
        badge = generate_badge_data("gpt-4o", "code_generation", board)

        assert badge["status"] == "ranked"
        assert badge["model"] == "gpt-4o"
        assert badge["rank"] == 2
        assert badge["quality_score"] == 0.85
        assert badge["cost_per_1k"] == 5.0
        assert badge["latency_ms"] == 400.0
        assert badge["sample_size"] == 100

    def test_missing_model_returns_not_ranked(self) -> None:
        board = [_entry(model="gpt-4o")]
        badge = generate_badge_data("missing", "code_generation", board)

        assert badge["status"] == "not_ranked"
        assert "missing" in badge["message"]

    def test_wrong_task_type_returns_not_ranked(self) -> None:
        board = [_entry(model="gpt-4o", task_type="summarization")]
        badge = generate_badge_data("gpt-4o", "code_generation", board)

        assert badge["status"] == "not_ranked"

    def test_verified_flag_propagated(self) -> None:
        board = [_entry(model="gpt-4o", verified=True)]
        badge = generate_badge_data("gpt-4o", "code_generation", board)

        assert badge["verified"] is True

    def test_empty_leaderboard(self) -> None:
        badge = generate_badge_data("any", "any", [])

        assert badge["status"] == "not_ranked"


# ---------------------------------------------------------------------------
# generate_summary_widget
# ---------------------------------------------------------------------------


class TestSummaryWidget:

    def test_top_model_per_task_type(self) -> None:
        board = [
            _entry(model="best-code", task_type="code_generation", rank=1),
            _entry(model="other-code", task_type="code_generation", rank=2),
            _entry(model="best-summ", task_type="summarization", rank=1),
        ]
        widget = generate_summary_widget(board)

        assert widget["top_models"]["code_generation"]["model"] == "best-code"
        assert widget["top_models"]["summarization"]["model"] == "best-summ"

    def test_total_counts(self) -> None:
        board = [
            _entry(model="a", task_type="code_generation", rank=1, sample_size=50),
            _entry(model="b", task_type="code_generation", rank=2, sample_size=30),
            _entry(model="a", task_type="summarization", rank=1, sample_size=40),
        ]
        widget = generate_summary_widget(board)

        assert widget["total_models"] == 2
        assert widget["total_task_types"] == 2
        assert widget["total_benchmarks"] == 120

    def test_empty_leaderboard(self) -> None:
        widget = generate_summary_widget([])

        assert widget["top_models"] == {}
        assert widget["total_models"] == 0
        assert widget["total_task_types"] == 0
        assert widget["total_benchmarks"] == 0

    def test_single_entry(self) -> None:
        board = [_entry(model="solo", rank=1)]
        widget = generate_summary_widget(board)

        assert len(widget["top_models"]) == 1
        assert widget["total_models"] == 1

    def test_verified_status_in_top_model(self) -> None:
        board = [_entry(model="verified-best", rank=1, verified=True)]
        widget = generate_summary_widget(board)

        assert widget["top_models"]["code_generation"]["verified"] is True
