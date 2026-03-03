"""Reusable query builders for stats, events, and aggregations.

All queries target TimescaleDB continuous aggregates where possible
for dashboard performance.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_summary_stats(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    group_by: str = "model",
    org_id: str | None = None,
) -> list[dict]:
    """Aggregated stats grouped by model, provider, or task_type."""
    valid_groups = {"model", "provider", "task_type"}
    if group_by not in valid_groups:
        raise ValueError(f"group_by must be one of {valid_groups}")

    org_filter = "AND org_id = :org_id" if org_id else ""

    query = text(f"""
        SELECT
            {group_by} AS key,
            COUNT(*) AS request_count,
            SUM(estimated_cost) AS total_cost_usd,
            AVG(latency_ms) AS avg_latency_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
            AVG(estimated_cost) AS avg_cost_per_request_usd,
            SUM(prompt_tokens) AS total_prompt_tokens,
            SUM(completion_tokens) AS total_completion_tokens,
            COUNT(*) FILTER (WHERE status = 'failure') AS failure_count
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
        {org_filter}
        GROUP BY {group_by}
        ORDER BY total_cost_usd DESC
    """)

    params: dict = {"start": start, "end": end}
    if org_id:
        params["org_id"] = org_id

    result = await session.execute(query, params)
    return [dict(row._mapping) for row in result.fetchall()]


async def get_timeseries(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    interval: str = "1h",
    metric: str = "cost",
    model: str | None = None,
    org_id: str | None = None,
) -> list[dict]:
    """Time-bucketed metric values for charts."""
    metric_map = {
        "cost": "SUM(estimated_cost)",
        "requests": "COUNT(*)",
        "latency": "AVG(latency_ms)",
        "tokens": "SUM(total_tokens)",
    }
    if metric not in metric_map:
        raise ValueError(f"metric must be one of {set(metric_map)}")

    interval_map = {"1h": "1 hour", "6h": "6 hours", "1d": "1 day"}
    pg_interval = interval_map.get(interval, "1 hour")

    filters = ["created_at >= :start", "created_at < :end"]
    params: dict = {"start": start, "end": end}

    if model:
        filters.append("model = :model")
        params["model"] = model
    if org_id:
        filters.append("org_id = :org_id")
        params["org_id"] = org_id

    where = " AND ".join(filters)

    query = text(f"""
        SELECT
            time_bucket('{pg_interval}', created_at) AS timestamp,
            {metric_map[metric]} AS value
        FROM llm_events
        WHERE {where}
        GROUP BY timestamp
        ORDER BY timestamp
    """)

    result = await session.execute(query, params)
    return [dict(row._mapping) for row in result.fetchall()]


async def get_top_traces(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    sort_by: str = "cost",
    limit: int = 10,
    org_id: str | None = None,
) -> list[dict]:
    """Most expensive/slow/token-heavy traces."""
    sort_map = {
        "cost": "total_cost_usd DESC",
        "tokens": "total_tokens DESC",
        "latency": "total_latency_ms DESC",
    }
    if sort_by not in sort_map:
        raise ValueError(f"sort_by must be one of {set(sort_map)}")

    org_filter = "AND org_id = :org_id" if org_id else ""

    query = text(f"""
        SELECT
            trace_id,
            SUM(estimated_cost) AS total_cost_usd,
            SUM(total_tokens) AS total_tokens,
            SUM(latency_ms) AS total_latency_ms,
            COUNT(*) AS event_count,
            array_agg(DISTINCT model) AS models_used,
            MIN(created_at) AS first_event_at,
            MAX(created_at) AS last_event_at,
            MAX(agent_framework) AS agent_framework
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
        {org_filter}
        GROUP BY trace_id
        ORDER BY {sort_map[sort_by]}
        LIMIT :limit
    """)

    params: dict = {"start": start, "end": end, "limit": limit}
    if org_id:
        params["org_id"] = org_id

    result = await session.execute(query, params)
    return [dict(row._mapping) for row in result.fetchall()]
