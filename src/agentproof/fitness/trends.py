"""Performance trend analysis over time.

Queries benchmark_results bucketed by day to show how a model's
quality, cost, and throughput evolve. Includes a simple linear
regression to detect quality improvement or degradation.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.fitness.types import TrendPoint
from agentproof.utils import utcnow


async def get_trends(
    session: AsyncSession,
    model: str,
    task_type: str | None = None,
    days: int = 30,
) -> list[TrendPoint]:
    """Fetch daily-bucketed benchmark trends for a model.

    Args:
        session: Async DB session.
        model: The model name to fetch trends for.
        task_type: Optional filter to a single task type.
        days: How far back to look (default 30).

    Returns:
        One TrendPoint per (day, task_type) bucket, ordered chronologically.
    """
    cutoff = utcnow() - timedelta(days=days)

    filters = [
        "benchmark_model = :model",
        "created_at >= :cutoff",
    ]
    params: dict = {"model": model, "cutoff": cutoff}

    if task_type:
        filters.append("task_type = :task_type")
        params["task_type"] = task_type

    where = " AND ".join(filters)

    query = text(f"""
        SELECT
            time_bucket('1 day', created_at) AS bucket,
            task_type,
            AVG(quality_score) AS avg_quality,
            AVG(benchmark_cost) AS avg_cost,
            COUNT(*) AS sample_size
        FROM benchmark_results
        WHERE {where}
        GROUP BY bucket, task_type
        ORDER BY bucket, task_type
    """)

    result = await session.execute(query, params)
    return [
        TrendPoint(
            timestamp=row["bucket"],
            model=model,
            task_type=row["task_type"],
            quality_score=float(row["avg_quality"] or 0),
            cost=float(row["avg_cost"] or 0),
            sample_size=int(row["sample_size"] or 0),
        )
        for row in [dict(r._mapping) for r in result.fetchall()]
    ]


def compute_trend_slope(points: list[TrendPoint]) -> float:
    """Simple linear regression slope on quality_score over time.

    Returns a positive value if quality is improving, negative if
    degrading. Zero when there are fewer than 2 data points.
    """
    if len(points) < 2:
        return 0.0

    # Use day-index as x-axis (0, 1, 2, ...) for numerical stability
    timestamps = [p.timestamp for p in points]
    base = min(timestamps)
    xs = [(t - base).total_seconds() / 86400.0 for t in timestamps]
    ys = [p.quality_score for p in points]

    n = len(xs)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)

    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    return round(slope, 8)
