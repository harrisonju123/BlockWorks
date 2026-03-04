"""Reusable query builders for stats, events, and aggregations.

All queries target TimescaleDB continuous aggregates where possible
for dashboard performance, falling back to raw llm_events only when
per-event granularity is required.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import json
import uuid

from agentproof.attestation.types import AttestationMetrics, TraceEvaluation
from agentproof.benchmarking.types import FitnessEntry
from agentproof.utils import utcnow

# asyncpg requires timedelta objects for interval bind params (not strings)
_INTERVAL_MAP: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
}


async def get_summary_stats(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    group_by: str = "model",
    org_id: str | None = None,
) -> list[dict]:
    """Aggregated stats grouped by model, provider, or task_type.

    Uses daily_summary when the time range spans >= 24 hours. Shorter ranges
    hit the raw table so that the most recent (not-yet-materialized) data is
    included.
    """
    valid_groups = {"model", "provider", "task_type"}
    if group_by not in valid_groups:
        raise ValueError(f"group_by must be one of {valid_groups}")

    use_aggregate = (end - start) > timedelta(hours=24)

    org_filter = "AND org_id = :org_id" if org_id else ""

    if use_aggregate:
        # daily_summary has: bucket, provider, model, task_type, org_id,
        # request_count, total_cost, avg_latency_ms, total_tokens,
        # failure_count, tool_call_count
        query = text(f"""
            SELECT
                {group_by} AS key,
                SUM(request_count) AS request_count,
                SUM(total_cost) AS total_cost_usd,
                SUM(avg_latency_ms * request_count) / NULLIF(SUM(request_count), 0) AS avg_latency_ms,
                NULL::DOUBLE PRECISION AS p95_latency_ms,
                SUM(total_cost) / NULLIF(SUM(request_count), 0) AS avg_cost_per_request_usd,
                NULL::BIGINT AS total_prompt_tokens,
                NULL::BIGINT AS total_completion_tokens,
                SUM(failure_count) AS failure_count
            FROM daily_summary
            WHERE bucket >= :start AND bucket < :end
            {org_filter}
            GROUP BY {group_by}
            ORDER BY total_cost_usd DESC
        """)
    else:
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
    """Time-bucketed metric values for charts.

    Routes to the appropriate continuous aggregate based on interval:
    - "1d" -> daily_summary (pre-aggregated daily buckets)
    - "1h" / "6h" -> hourly_model_stats (pre-aggregated hourly buckets)

    The pg_interval is passed as a bind parameter to avoid SQL interpolation.
    """
    pg_interval = _INTERVAL_MAP.get(interval, timedelta(hours=1))

    if interval == "1d":
        # daily_summary columns: bucket, provider, model, task_type, org_id,
        # request_count, total_cost, avg_latency_ms, total_tokens, failure_count
        agg_metric_map = {
            "cost": "SUM(total_cost)",
            "requests": "SUM(request_count)",
            "latency": "SUM(avg_latency_ms * request_count) / NULLIF(SUM(request_count), 0)",
            "tokens": "SUM(total_tokens)",
        }
        if metric not in agg_metric_map:
            raise ValueError(f"metric must be one of {set(agg_metric_map)}")

        filters = ["bucket >= :start", "bucket < :end"]
        params: dict = {"start": start, "end": end, "bucket_interval": pg_interval}

        if model:
            filters.append("model = :model")
            params["model"] = model
        if org_id:
            filters.append("org_id = :org_id")
            params["org_id"] = org_id

        where = " AND ".join(filters)

        query = text(f"""
            SELECT
                time_bucket(:bucket_interval, bucket) AS timestamp,
                {agg_metric_map[metric]} AS value
            FROM daily_summary
            WHERE {where}
            GROUP BY timestamp
            ORDER BY timestamp
        """)

    else:
        # hourly_model_stats columns: bucket, model, provider,
        # request_count, total_cost, avg_latency_ms, total_prompt_tokens,
        # total_completion_tokens, failure_count
        agg_metric_map = {
            "cost": "SUM(total_cost)",
            "requests": "SUM(request_count)",
            "latency": "SUM(avg_latency_ms * request_count) / NULLIF(SUM(request_count), 0)",
            "tokens": "SUM(total_prompt_tokens + total_completion_tokens)",
        }
        if metric not in agg_metric_map:
            raise ValueError(f"metric must be one of {set(agg_metric_map)}")

        filters = ["bucket >= :start", "bucket < :end"]
        params = {"start": start, "end": end, "bucket_interval": pg_interval}

        if model:
            filters.append("model = :model")
            params["model"] = model
        if org_id:
            # hourly_model_stats doesn't carry org_id, fall back to raw table
            return await _get_timeseries_raw(
                session, start, end, interval, metric, model, org_id
            )

        where = " AND ".join(filters)

        query = text(f"""
            SELECT
                time_bucket(:bucket_interval, bucket) AS timestamp,
                {agg_metric_map[metric]} AS value
            FROM hourly_model_stats
            WHERE {where}
            GROUP BY timestamp
            ORDER BY timestamp
        """)

    result = await session.execute(query, params)
    return [dict(row._mapping) for row in result.fetchall()]


async def _get_timeseries_raw(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    interval: str,
    metric: str,
    model: str | None = None,
    org_id: str | None = None,
) -> list[dict]:
    """Fallback to raw llm_events when aggregates lack the needed columns (e.g. org_id on hourly)."""
    metric_map = {
        "cost": "SUM(estimated_cost)",
        "requests": "COUNT(*)",
        "latency": "AVG(latency_ms)",
        "tokens": "SUM(total_tokens)",
    }
    if metric not in metric_map:
        raise ValueError(f"metric must be one of {set(metric_map)}")

    pg_interval = _INTERVAL_MAP.get(interval, timedelta(hours=1))

    filters = ["created_at >= :start", "created_at < :end"]
    params: dict = {"start": start, "end": end, "bucket_interval": pg_interval}

    if model:
        filters.append("model = :model")
        params["model"] = model
    if org_id:
        filters.append("org_id = :org_id")
        params["org_id"] = org_id

    where = " AND ".join(filters)

    query = text(f"""
        SELECT
            time_bucket(:bucket_interval, created_at) AS timestamp,
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
    """Most expensive/slow/token-heavy traces.

    Stays on raw llm_events -- trace-level grouping requires per-event granularity.
    """
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


async def get_waste_analysis(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    org_id: str | None = None,
) -> list[dict]:
    """Per (task_type, model) aggregates needed by the waste scorer.

    Stays on raw llm_events -- the waste scorer needs task_type_confidence
    which isn't available in continuous aggregates.
    """
    org_filter = "AND org_id = :org_id" if org_id else ""

    query = text(f"""
        SELECT
            task_type,
            model,
            COUNT(*) AS call_count,
            SUM(estimated_cost) AS total_cost,
            AVG(task_type_confidence) AS avg_confidence
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
          AND task_type IS NOT NULL
          {org_filter}
        GROUP BY task_type, model
        ORDER BY total_cost DESC
    """)

    params: dict = {"start": start, "end": end}
    if org_id:
        params["org_id"] = org_id

    result = await session.execute(query, params)
    return [dict(row._mapping) for row in result.fetchall()]


async def get_mcp_server_stats(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Per-server P50/P95 latency, failure rate, and call count.

    Queries the raw mcp_calls table since there is no continuous aggregate
    for MCP data (low enough cardinality that it doesn't need one yet).
    """
    query = text("""
        SELECT
            server_name,
            COUNT(*) AS call_count,
            COUNT(*) FILTER (WHERE status = 'failure') AS failure_count,
            CASE
                WHEN COUNT(*) > 0
                THEN COUNT(*) FILTER (WHERE status = 'failure')::DOUBLE PRECISION / COUNT(*)
                ELSE 0.0
            END AS failure_rate,
            AVG(latency_ms) AS avg_latency_ms,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
        FROM mcp_calls
        WHERE created_at >= :start AND created_at < :end
        GROUP BY server_name
        ORDER BY call_count DESC
    """)

    result = await session.execute(query, {"start": start, "end": end})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_mcp_execution_graph(
    session: AsyncSession,
    trace_id: str,
) -> dict:
    """Full execution DAG for an MCP trace.

    Returns the nodes (mcp_calls) and edges (mcp_execution_graph)
    for a given trace_id so the frontend can render the DAG.
    """
    nodes_query = text("""
        SELECT
            id, event_id, created_at, server_name, method,
            params_hash, response_hash, latency_ms,
            response_tokens, status, error_type
        FROM mcp_calls
        WHERE trace_id = :trace_id
        ORDER BY created_at
    """)

    edges_query = text("""
        SELECT id, parent_call_id, child_call_id, trace_id
        FROM mcp_execution_graph
        WHERE trace_id = :trace_id
    """)

    nodes_result = await session.execute(nodes_query, {"trace_id": trace_id})
    edges_result = await session.execute(edges_query, {"trace_id": trace_id})

    return {
        "trace_id": trace_id,
        "nodes": [dict(row._mapping) for row in nodes_result.fetchall()],
        "edges": [dict(row._mapping) for row in edges_result.fetchall()],
    }


async def get_mcp_unused_data(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """MCP responses where response_tokens > 0 but the data wasn't
    referenced in subsequent LLM calls (wasted context detection).

    Heuristic: an MCP call's response is "unused" when it has non-zero
    response_tokens but no child edge in the execution graph consumed it,
    and the trace's next LLM event didn't incorporate the response hash
    in its prompt. For the initial implementation we use the simpler
    proxy: calls with high response_tokens that have no child edges.
    """
    query = text("""
        SELECT
            mc.server_name,
            mc.method,
            COUNT(*) AS unused_call_count,
            SUM(mc.response_tokens) AS total_wasted_tokens,
            AVG(mc.response_tokens) AS avg_wasted_tokens
        FROM mcp_calls mc
        LEFT JOIN mcp_execution_graph eg
            ON eg.parent_call_id = mc.id AND eg.trace_id = mc.trace_id
        WHERE mc.created_at >= :start AND mc.created_at < :end
          AND mc.response_tokens > 0
          AND mc.status = 'success'
          AND eg.parent_call_id IS NULL
        GROUP BY mc.server_name, mc.method
        ORDER BY total_wasted_tokens DESC
    """)

    result = await session.execute(query, {"start": start, "end": end})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_fitness_matrix(
    session: AsyncSession,
    org_id: str | None = None,
) -> list[FitnessEntry]:
    """Read the fitness matrix continuous aggregate.

    Returns one FitnessEntry per (model, task_type) combination, aggregated
    across all available time buckets. The routing engine and waste scorer
    consume this to pick optimal models for each task type.
    """
    params: dict = {}

    # The fitness_matrix view is built from benchmark_results, which carries org_id.
    # For org-scoped queries we fall back to the raw table since the view aggregates
    # across all orgs.
    if org_id:
        params["org_id"] = org_id

        query = text("""
            SELECT
                benchmark_model AS model,
                task_type,
                AVG(quality_score) AS avg_quality,
                AVG(benchmark_cost) AS avg_cost,
                AVG(benchmark_latency_ms) AS avg_latency,
                COUNT(*) AS sample_size
            FROM benchmark_results
            WHERE org_id = :org_id
            GROUP BY benchmark_model, task_type
            ORDER BY task_type, avg_quality DESC
        """)
    else:
        query = text("""
            SELECT
                model,
                task_type,
                AVG(avg_quality) AS avg_quality,
                AVG(avg_cost) AS avg_cost,
                AVG(avg_latency) AS avg_latency,
                SUM(sample_size) AS sample_size
            FROM fitness_matrix
            GROUP BY model, task_type
            ORDER BY task_type, avg_quality DESC
        """)

    result = await session.execute(query, params)
    return [
        FitnessEntry(
            task_type=row["task_type"],
            model=row["model"],
            avg_quality=float(row["avg_quality"] or 0),
            avg_cost=float(row["avg_cost"] or 0),
            avg_latency=float(row["avg_latency"] or 0),
            sample_size=int(row["sample_size"] or 0),
        )
        for row in [dict(r._mapping) for r in result.fetchall()]
    ]


async def get_benchmark_results(
    session: AsyncSession,
    org_id: str | None = None,
    task_type: str | None = None,
    benchmark_model: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Paginated benchmark results with optional filters.

    Returns (rows, total_count) for the API pagination response.
    """
    filters: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if org_id:
        filters.append("org_id = :org_id")
        params["org_id"] = org_id
    if task_type:
        filters.append("task_type = :task_type")
        params["task_type"] = task_type
    if benchmark_model:
        filters.append("benchmark_model = :benchmark_model")
        params["benchmark_model"] = benchmark_model

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    count_query = text(f"""
        SELECT COUNT(*) AS cnt FROM benchmark_results {where_clause}
    """)
    count_result = await session.execute(count_query, params)
    total_count = count_result.scalar() or 0

    data_query = text(f"""
        SELECT
            id, created_at, original_event_id, original_model,
            benchmark_model, task_type, quality_score,
            original_cost, benchmark_cost,
            original_latency_ms, benchmark_latency_ms,
            judge_model, rubric_version, org_id
        FROM benchmark_results
        {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(data_query, params)
    rows = [dict(row._mapping) for row in result.fetchall()]
    return rows, total_count


async def get_duplicate_tool_calls(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Find tool calls with identical args_hash within the same trace.

    Groups by (trace_id, tool_name, args_hash) and returns only those
    with dup_count >= 2. Joins back to llm_events for cost estimation.
    """
    query = text("""
        SELECT
            e.trace_id,
            tc.tool_name,
            tc.args_hash,
            COUNT(*) AS dup_count,
            AVG(e.estimated_cost) AS estimated_cost_per_call
        FROM tool_calls tc
        JOIN llm_events e
            ON e.id = tc.event_id AND e.created_at = tc.created_at
        WHERE tc.created_at >= :start AND tc.created_at < :end
        GROUP BY e.trace_id, tc.tool_name, tc.args_hash
        HAVING COUNT(*) >= 2
        ORDER BY dup_count DESC
    """)

    result = await session.execute(query, {"start": start, "end": end})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_prompt_hash_duplicates(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    *,
    window_hours: int = 1,
) -> list[dict]:
    """Find identical prompt_hash values repeated within a time window.

    Groups events by prompt_hash and only returns those that appear
    more than once within window_hours of each other. This surfaces
    cache misses — prompts that could have been served from cache.
    """
    query = text("""
        WITH windowed AS (
            SELECT
                prompt_hash,
                COUNT(*) AS dup_count,
                SUM(estimated_cost) AS total_cost,
                array_agg(DISTINCT model) AS models,
                array_agg(DISTINCT trace_id) AS trace_ids,
                MIN(created_at) AS first_seen,
                MAX(created_at) AS last_seen
            FROM llm_events
            WHERE created_at >= :start AND created_at < :end
              AND prompt_hash IS NOT NULL
            GROUP BY prompt_hash
            HAVING COUNT(*) >= 2
               AND (MAX(created_at) - MIN(created_at)) <= :window
        )
        SELECT *
        FROM windowed
        ORDER BY total_cost DESC
    """)

    result = await session.execute(
        query, {"start": start, "end": end, "window": timedelta(hours=window_hours)}
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def get_trace_tool_patterns(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Per-trace tool call sequences for agent loop detection.

    Returns one row per (trace_id, tool_name) pair where the tool
    was called >= 3 times. Includes the ordered list of args_hashes
    so the detector can check for similar-but-not-identical patterns.
    """
    query = text("""
        SELECT
            e.trace_id,
            tc.tool_name,
            array_agg(tc.args_hash ORDER BY tc.created_at) AS args_hashes,
            COUNT(*) AS call_count,
            SUM(e.estimated_cost) AS total_cost,
            AVG(e.estimated_cost) AS estimated_cost_per_call
        FROM tool_calls tc
        JOIN llm_events e
            ON e.id = tc.event_id AND e.created_at = tc.created_at
        WHERE tc.created_at >= :start AND tc.created_at < :end
        GROUP BY e.trace_id, tc.tool_name
        HAVING COUNT(*) >= 3
        ORDER BY total_cost DESC
    """)

    result = await session.execute(query, {"start": start, "end": end})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_distinct_org_ids(session: AsyncSession) -> list[str]:
    """Return all distinct org_id values from llm_events."""
    query = text("SELECT DISTINCT org_id FROM llm_events WHERE org_id IS NOT NULL")
    result = await session.execute(query)
    return [row[0] for row in result.fetchall()]


async def get_earliest_event_time(
    session: AsyncSession,
    org_id: str | None = None,
) -> datetime | None:
    """Return the earliest event timestamp, optionally scoped to an org."""
    if org_id:
        query = text("SELECT MIN(created_at) FROM llm_events WHERE org_id = :org_id")
        result = await session.execute(query, {"org_id": org_id})
    else:
        query = text("SELECT MIN(created_at) FROM llm_events")
        result = await session.execute(query)
    val = result.scalar()
    return val


async def get_attestation_metrics(
    session: AsyncSession,
    org_id: str,
    start: datetime,
    end: datetime,
) -> AttestationMetrics:
    """Aggregate metrics for one org over one period, for attestation hashing.

    Pulls totals from daily_summary when the range spans >= 24h, otherwise
    falls back to raw llm_events. The waste score comes from a separate
    subquery against the waste analysis path.
    """
    use_aggregate = (end - start) > timedelta(hours=24)

    if use_aggregate:
        query = text("""
            SELECT
                COALESCE(SUM(total_cost), 0)         AS total_spend,
                COALESCE(SUM(request_count), 0)       AS request_count,
                CASE
                    WHEN SUM(request_count) > 0
                    THEN SUM(failure_count)::DOUBLE PRECISION / SUM(request_count)
                    ELSE 0.0
                END                                   AS failure_rate
            FROM daily_summary
            WHERE bucket >= :start AND bucket < :end
              AND org_id = :org_id
        """)
    else:
        query = text("""
            SELECT
                COALESCE(SUM(estimated_cost), 0)      AS total_spend,
                COUNT(*)                               AS request_count,
                CASE
                    WHEN COUNT(*) > 0
                    THEN COUNT(*) FILTER (WHERE status = 'failure')::DOUBLE PRECISION / COUNT(*)
                    ELSE 0.0
                END                                   AS failure_rate
            FROM llm_events
            WHERE created_at >= :start AND created_at < :end
              AND org_id = :org_id
        """)

    result = await session.execute(query, {"start": start, "end": end, "org_id": org_id})
    row = dict(result.fetchone()._mapping)

    # Model distribution: request count per model
    dist_query = text("""
        SELECT model, COUNT(*) AS cnt
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
          AND org_id = :org_id
        GROUP BY model
    """)
    dist_result = await session.execute(
        dist_query, {"start": start, "end": end, "org_id": org_id}
    )
    model_distribution = {
        r["model"]: int(r["cnt"])
        for r in [dict(r._mapping) for r in dist_result.fetchall()]
    }

    # Waste score: reuse the existing waste analysis path
    waste_rows = await get_waste_analysis(session, start, end, org_id=org_id)
    total_spend = float(row["total_spend"])
    total_savings = 0.0
    for wr in waste_rows:
        total_savings += float(wr.get("total_cost", 0))
    waste_score = min(total_savings / total_spend, 1.0) if total_spend > 0 else 0.0

    return AttestationMetrics(
        total_spend=total_spend,
        waste_score=waste_score,
        request_count=int(row["request_count"]),
        failure_rate=float(row["failure_rate"]),
        model_distribution=model_distribution,
    )


async def get_trace_evaluations(
    session: AsyncSession,
    org_id: str,
    start: datetime,
    end: datetime,
) -> list[TraceEvaluation]:
    """Trace-level evaluation data for Merkle tree leaves.

    Groups llm_events by trace_id to produce one evaluation per trace.
    Each row becomes a leaf in the attestation's Merkle tree.
    """
    query = text("""
        SELECT
            trace_id,
            MODE() WITHIN GROUP (ORDER BY model)       AS model,
            MODE() WITHIN GROUP (ORDER BY task_type)    AS task_type,
            SUM(estimated_cost)                         AS cost,
            COALESCE(AVG(task_type_confidence), 0.0)    AS quality_score,
            MIN(created_at)                             AS timestamp
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
          AND org_id = :org_id
          AND trace_id IS NOT NULL
        GROUP BY trace_id
        ORDER BY trace_id
    """)

    result = await session.execute(
        query, {"start": start, "end": end, "org_id": org_id}
    )
    return [
        TraceEvaluation(
            trace_id=row["trace_id"],
            model=row["model"] or "unknown",
            task_type=row["task_type"] or "unknown",
            cost=float(row["cost"] or 0),
            quality_score=float(row["quality_score"] or 0),
            timestamp=row["timestamp"],
        )
        for row in [dict(r._mapping) for r in result.fetchall()]
    ]


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


async def insert_alert_rule(
    session: AsyncSession,
    *,
    org_id: str,
    rule_type: str,
    threshold_config: dict,
    channel: str,
    webhook_url: str | None,
    enabled: bool,
) -> dict:
    """Insert a new alert rule and return the created row."""
    rule_id = str(uuid.uuid4())
    now = utcnow()

    query = text("""
        INSERT INTO alert_rules
            (id, org_id, rule_type, threshold_config, channel, webhook_url, enabled, created_at, updated_at)
        VALUES
            (:id, :org_id, :rule_type, :threshold_config::jsonb, :channel, :webhook_url, :enabled, :created_at, :updated_at)
        RETURNING *
    """)

    result = await session.execute(query, {
        "id": rule_id,
        "org_id": org_id,
        "rule_type": rule_type,
        "threshold_config": json.dumps(threshold_config),
        "channel": channel,
        "webhook_url": webhook_url,
        "enabled": enabled,
        "created_at": now,
        "updated_at": now,
    })
    await session.commit()
    return dict(result.fetchone()._mapping)


async def get_alert_rules(
    session: AsyncSession,
    org_id: str | None = None,
) -> list[dict]:
    """Fetch all alert rules, optionally filtered by org_id."""
    if org_id:
        query = text("""
            SELECT * FROM alert_rules
            WHERE org_id = :org_id
            ORDER BY created_at DESC
        """)
        result = await session.execute(query, {"org_id": org_id})
    else:
        query = text("SELECT * FROM alert_rules ORDER BY created_at DESC")
        result = await session.execute(query)

    return [dict(row._mapping) for row in result.fetchall()]


async def get_alert_rule_by_id(
    session: AsyncSession,
    rule_id: str,
) -> dict | None:
    """Fetch a single alert rule by primary key. Returns None if not found."""
    query = text("SELECT * FROM alert_rules WHERE id = :id")
    result = await session.execute(query, {"id": rule_id})
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def update_alert_rule(
    session: AsyncSession,
    rule_id: str,
    **fields: object,
) -> dict | None:
    """Patch an alert rule with the given fields. Returns the updated row or None."""
    allowed = {"threshold_config", "channel", "webhook_url", "enabled"}
    set_parts: list[str] = []
    params: dict = {"id": rule_id, "updated_at": utcnow()}

    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "threshold_config":
            set_parts.append("threshold_config = :threshold_config::jsonb")
            params["threshold_config"] = json.dumps(value)
        else:
            set_parts.append(f"{key} = :{key}")
            params[key] = value

    if not set_parts:
        return await get_alert_rule_by_id(session, rule_id)

    set_parts.append("updated_at = :updated_at")
    set_clause = ", ".join(set_parts)

    query = text(f"""
        UPDATE alert_rules
        SET {set_clause}
        WHERE id = :id
        RETURNING *
    """)

    result = await session.execute(query, params)
    await session.commit()
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def delete_alert_rule(
    session: AsyncSession,
    rule_id: str,
) -> bool:
    """Delete an alert rule. Returns True if a row was deleted."""
    query = text("DELETE FROM alert_rules WHERE id = :id")
    result = await session.execute(query, {"id": rule_id})
    await session.commit()
    return result.rowcount > 0


# ---------------------------------------------------------------------------
# Alert history
# ---------------------------------------------------------------------------


async def get_alert_history(
    session: AsyncSession,
    org_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Paginated alert history, optionally scoped to rules belonging to an org.

    Returns (rows, total_count).
    """
    org_join = ""
    org_filter = ""
    params: dict = {"limit": limit, "offset": offset}

    if org_id:
        org_join = "JOIN alert_rules ar ON ar.id = ah.rule_id"
        org_filter = "WHERE ar.org_id = :org_id"
        params["org_id"] = org_id

    count_query = text(f"""
        SELECT COUNT(*) AS cnt
        FROM alert_history ah
        {org_join}
        {org_filter}
    """)
    count_result = await session.execute(count_query, params)
    total_count = count_result.scalar() or 0

    data_query = text(f"""
        SELECT ah.*
        FROM alert_history ah
        {org_join}
        {org_filter}
        ORDER BY ah.triggered_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await session.execute(data_query, params)
    rows = [dict(row._mapping) for row in result.fetchall()]
    return rows, total_count


# ---------------------------------------------------------------------------
# Budget configs
# ---------------------------------------------------------------------------


async def insert_budget_config(
    session: AsyncSession,
    *,
    org_id: str,
    project_id: str | None,
    budget_usd: float,
    period: str,
    action: str,
) -> dict:
    """Insert a new budget config and return the created row."""
    budget_id = str(uuid.uuid4())
    now = utcnow()

    query = text("""
        INSERT INTO budget_configs
            (id, org_id, project_id, budget_usd, period, action, current_spend, period_start, created_at)
        VALUES
            (:id, :org_id, :project_id, :budget_usd, :period, :action, 0.0, :period_start, :created_at)
        RETURNING *
    """)

    result = await session.execute(query, {
        "id": budget_id,
        "org_id": org_id,
        "project_id": project_id,
        "budget_usd": budget_usd,
        "period": period,
        "action": action,
        "period_start": now,
        "created_at": now,
    })
    await session.commit()
    return dict(result.fetchone()._mapping)


async def get_budget_configs(
    session: AsyncSession,
    org_id: str | None = None,
) -> list[dict]:
    """Fetch all budget configs, optionally filtered by org_id."""
    if org_id:
        query = text("""
            SELECT * FROM budget_configs
            WHERE org_id = :org_id
            ORDER BY created_at DESC
        """)
        result = await session.execute(query, {"org_id": org_id})
    else:
        query = text("SELECT * FROM budget_configs ORDER BY created_at DESC")
        result = await session.execute(query)

    return [dict(row._mapping) for row in result.fetchall()]


async def get_budget_by_id(
    session: AsyncSession,
    budget_id: str,
) -> dict | None:
    """Fetch a single budget config by primary key. Returns None if not found."""
    query = text("SELECT * FROM budget_configs WHERE id = :id")
    result = await session.execute(query, {"id": budget_id})
    row = result.fetchone()
    return dict(row._mapping) if row else None


# ---------------------------------------------------------------------------
# Benchmark config (singleton row in benchmark_config table)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Routing policies & decisions
# ---------------------------------------------------------------------------


async def get_active_routing_policy(session: AsyncSession) -> dict | None:
    """Fetch the currently active routing policy. Returns None if none exists."""
    query = text("""
        SELECT id, policy_json, version, is_active, created_at
        FROM routing_policies
        WHERE is_active = TRUE
        LIMIT 1
    """)
    result = await session.execute(query)
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def upsert_routing_policy(
    session: AsyncSession,
    policy_json: dict,
    version: int,
) -> dict:
    """Insert a new active policy, deactivating any previously active one.

    Uses a transaction to ensure exactly one active policy at a time.
    Returns the newly created row.
    """
    now = utcnow()
    policy_id = str(uuid.uuid4())

    # Deactivate the current active policy (if any)
    await session.execute(
        text("UPDATE routing_policies SET is_active = FALSE WHERE is_active = TRUE")
    )

    query = text("""
        INSERT INTO routing_policies (id, policy_json, version, is_active, created_at)
        VALUES (:id, :policy_json::jsonb, :version, TRUE, :created_at)
        RETURNING *
    """)
    result = await session.execute(query, {
        "id": policy_id,
        "policy_json": json.dumps(policy_json),
        "version": version,
        "created_at": now,
    })
    await session.commit()
    return dict(result.fetchone()._mapping)


async def insert_routing_decisions(
    session: AsyncSession,
    decisions: list[dict],
) -> None:
    """Bulk-insert routing decisions. Used by the writer's fallback path.

    For high-throughput the RoutingDecisionWriter uses asyncpg COPY directly;
    this function exists for the individual-fallback retry path and tests.
    """
    if not decisions:
        return

    query = text("""
        INSERT INTO routing_decisions
            (id, created_at, task_type, requested_model, selected_model,
             was_overridden, reason, policy_version, group_name)
        VALUES
            (:id, :created_at, :task_type, :requested_model, :selected_model,
             :was_overridden, :reason, :policy_version, :group_name)
    """)

    now = utcnow()
    for d in decisions:
        await session.execute(query, {
            "id": str(d.get("id", uuid.uuid4())),
            "created_at": d.get("created_at", now),
            "task_type": d.get("task_type"),
            "requested_model": d["requested_model"],
            "selected_model": d["selected_model"],
            "was_overridden": d.get("was_overridden", False),
            "reason": d.get("reason"),
            "policy_version": d.get("policy_version"),
            "group_name": d.get("group_name"),
        })
    await session.commit()


async def get_routing_decisions(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Paginated routing decisions, most recent first.

    Returns (rows, total_count) for the API pagination response.
    Uses a window function to get the count in a single round-trip.
    """
    query = text("""
        SELECT id, created_at, task_type, requested_model, selected_model,
               was_overridden, reason, policy_version, group_name,
               COUNT(*) OVER() AS _total_count
        FROM routing_decisions
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await session.execute(query, {"limit": limit, "offset": offset})
    raw_rows = result.fetchall()

    total_count = int(raw_rows[0]._mapping["_total_count"]) if raw_rows else 0
    rows = [{k: v for k, v in row._mapping.items() if k != "_total_count"} for row in raw_rows]
    return rows, total_count


async def get_benchmark_config_from_db(session: AsyncSession) -> dict | None:
    """Fetch the singleton benchmark config row. Returns None if not yet created."""
    query = text("SELECT * FROM benchmark_config WHERE id = 'default'")
    result = await session.execute(query)
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def upsert_benchmark_config(
    session: AsyncSession,
    *,
    sample_rate: float | None = None,
    benchmark_models: list[str] | None = None,
    judge_model: str | None = None,
    enabled_task_types: list[str] | None = None,
) -> dict:
    """Insert or update the singleton benchmark config row.

    Only provided (non-None) fields are updated. Returns the full row after upsert.
    """
    query = text("""
        INSERT INTO benchmark_config (id, sample_rate, benchmark_models, judge_model, enabled_task_types)
        VALUES (
            'default',
            COALESCE(:sample_rate, 0.1),
            COALESCE(:benchmark_models, ARRAY['claude-haiku-4-5-20251001']::text[]),
            COALESCE(:judge_model, 'claude-haiku-4-5-20251001'),
            COALESCE(:enabled_task_types, ARRAY[]::text[])
        )
        ON CONFLICT (id) DO UPDATE SET
            sample_rate = COALESCE(:sample_rate, benchmark_config.sample_rate),
            benchmark_models = COALESCE(:benchmark_models, benchmark_config.benchmark_models),
            judge_model = COALESCE(:judge_model, benchmark_config.judge_model),
            enabled_task_types = COALESCE(:enabled_task_types, benchmark_config.enabled_task_types),
            updated_at = :now
        RETURNING *
    """)
    now = utcnow()
    result = await session.execute(query, {
        "sample_rate": sample_rate,
        "benchmark_models": benchmark_models,
        "judge_model": judge_model,
        "enabled_task_types": enabled_task_types,
        "now": now,
    })
    await session.commit()
    row = result.fetchone()
    return dict(row._mapping)
