"""Tests for trend analysis.

Validates slope computation with mock DB data, including edge cases
like empty data, single point, and flat/improving/degrading trends.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentproof.fitness.trends import compute_trend_slope, get_trends
from agentproof.fitness.types import TrendPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_row(**kwargs) -> MagicMock:
    """Build a mock row that supports dict(row._mapping)."""
    row = MagicMock()
    row._mapping = kwargs
    return row


def _make_point(
    day_offset: int = 0,
    quality: float = 0.8,
    model: str = "test-model",
    task_type: str = "code_generation",
) -> TrendPoint:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TrendPoint(
        timestamp=base + timedelta(days=day_offset),
        model=model,
        task_type=task_type,
        quality_score=quality,
        cost=0.001,
        sample_size=10,
    )


# ---------------------------------------------------------------------------
# get_trends (DB query)
# ---------------------------------------------------------------------------


class TestGetTrends:

    @pytest.mark.asyncio
    async def test_returns_trend_points(self) -> None:
        now = datetime.now(timezone.utc)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                bucket=now - timedelta(days=2),
                task_type="code_generation",
                avg_quality=0.80,
                avg_cost=0.001,
                sample_size=50,
            ),
            _mock_row(
                bucket=now - timedelta(days=1),
                task_type="code_generation",
                avg_quality=0.85,
                avg_cost=0.0012,
                sample_size=60,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        points = await get_trends(session, model="gpt-4o")
        assert len(points) == 2
        assert all(isinstance(p, TrendPoint) for p in points)
        assert points[0].model == "gpt-4o"
        assert points[1].quality_score == pytest.approx(0.85)

    @pytest.mark.asyncio
    async def test_filters_by_task_type(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        session = AsyncMock()
        session.execute.return_value = mock_result

        await get_trends(session, model="gpt-4o", task_type="summarization")

        call_args = session.execute.call_args
        query_text = str(call_args.args[0].text)
        assert "task_type" in query_text
        params = call_args.args[1]
        assert params["task_type"] == "summarization"

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        session = AsyncMock()
        session.execute.return_value = mock_result

        points = await get_trends(session, model="nonexistent")
        assert points == []

    @pytest.mark.asyncio
    async def test_null_values_default_to_zero(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                bucket=datetime.now(timezone.utc),
                task_type="extraction",
                avg_quality=None,
                avg_cost=None,
                sample_size=None,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        points = await get_trends(session, model="test")
        assert points[0].quality_score == 0.0
        assert points[0].cost == 0.0
        assert points[0].sample_size == 0


# ---------------------------------------------------------------------------
# compute_trend_slope
# ---------------------------------------------------------------------------


class TestTrendSlope:

    def test_improving_trend(self) -> None:
        """Quality goes from 0.6 to 0.9 over 3 days -> positive slope."""
        points = [
            _make_point(day_offset=0, quality=0.6),
            _make_point(day_offset=1, quality=0.7),
            _make_point(day_offset=2, quality=0.8),
            _make_point(day_offset=3, quality=0.9),
        ]
        slope = compute_trend_slope(points)
        assert slope > 0

    def test_degrading_trend(self) -> None:
        """Quality goes from 0.9 to 0.6 over 3 days -> negative slope."""
        points = [
            _make_point(day_offset=0, quality=0.9),
            _make_point(day_offset=1, quality=0.8),
            _make_point(day_offset=2, quality=0.7),
            _make_point(day_offset=3, quality=0.6),
        ]
        slope = compute_trend_slope(points)
        assert slope < 0

    def test_flat_trend(self) -> None:
        """Constant quality -> slope near zero."""
        points = [
            _make_point(day_offset=0, quality=0.8),
            _make_point(day_offset=1, quality=0.8),
            _make_point(day_offset=2, quality=0.8),
        ]
        slope = compute_trend_slope(points)
        assert slope == pytest.approx(0.0, abs=1e-6)

    def test_single_point_returns_zero(self) -> None:
        points = [_make_point()]
        slope = compute_trend_slope(points)
        assert slope == 0.0

    def test_empty_returns_zero(self) -> None:
        slope = compute_trend_slope([])
        assert slope == 0.0

    def test_two_points_linear(self) -> None:
        """With exactly 2 points, slope should match the delta."""
        points = [
            _make_point(day_offset=0, quality=0.5),
            _make_point(day_offset=1, quality=0.7),
        ]
        slope = compute_trend_slope(points)
        # Slope = (0.7 - 0.5) / (1 - 0) = 0.2 per day
        assert slope == pytest.approx(0.2, abs=1e-6)
