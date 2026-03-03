"""Stats endpoints: summary, timeseries, top traces, waste score, waste details."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.api.deps import get_db, resolve_time_range
from agentproof.api.schemas import (
    Period,
    StatGroup,
    SummaryResponse,
    TimeseriesPoint,
    TimeseriesResponse,
    TopTracesResponse,
    TraceInfo,
    WasteDetailItem,
    WasteReportResponse,
    WasteScoreResponse,
)
from agentproof.api.waste import compute_waste_score
from agentproof.db.queries import get_summary_stats, get_timeseries, get_top_traces, get_waste_analysis
from agentproof.waste.analyzer import WasteAnalyzer

router = APIRouter(prefix="/stats")


@router.get("/summary", response_model=SummaryResponse)
async def summary(
    start: datetime | None = None,
    end: datetime | None = None,
    org_id: str | None = None,
    group_by: str = Query(default="model", pattern="^(model|provider|task_type)$"),
    db: AsyncSession = Depends(get_db),
) -> SummaryResponse:
    start, end = resolve_time_range(start, end)
    rows = await get_summary_stats(db, start, end, group_by, org_id)

    groups = [
        StatGroup(
            key=row["key"] or "unknown",
            request_count=row["request_count"],
            total_cost_usd=float(row["total_cost_usd"] or 0),
            avg_latency_ms=float(row["avg_latency_ms"] or 0),
            p95_latency_ms=float(row["p95_latency_ms"] or 0),
            avg_cost_per_request_usd=float(row["avg_cost_per_request_usd"] or 0),
            total_prompt_tokens=int(row["total_prompt_tokens"] or 0),
            total_completion_tokens=int(row["total_completion_tokens"] or 0),
            failure_count=int(row["failure_count"] or 0),
        )
        for row in rows
    ]

    total_requests = sum(g.request_count for g in groups)
    total_cost = sum(g.total_cost_usd for g in groups)
    total_tokens = sum(g.total_prompt_tokens + g.total_completion_tokens for g in groups)
    total_failures = sum(g.failure_count for g in groups)

    return SummaryResponse(
        period=Period(start=start, end=end),
        total_requests=total_requests,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        failure_rate=total_failures / total_requests if total_requests > 0 else 0,
        groups=groups,
    )


@router.get("/timeseries", response_model=TimeseriesResponse)
async def timeseries(
    start: datetime | None = None,
    end: datetime | None = None,
    interval: str = Query(default="1h", pattern="^(1h|6h|1d)$"),
    metric: str = Query(default="cost", pattern="^(cost|requests|latency|tokens)$"),
    model: str | None = None,
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> TimeseriesResponse:
    start, end = resolve_time_range(start, end)
    rows = await get_timeseries(db, start, end, interval, metric, model, org_id)

    return TimeseriesResponse(
        metric=metric,
        interval=interval,
        data=[
            TimeseriesPoint(timestamp=row["timestamp"], value=float(row["value"] or 0))
            for row in rows
        ],
    )


@router.get("/top-traces", response_model=TopTracesResponse)
async def top_traces(
    start: datetime | None = None,
    end: datetime | None = None,
    sort_by: str = Query(default="cost", pattern="^(cost|tokens|latency)$"),
    limit: int = Query(default=10, ge=1, le=100),
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> TopTracesResponse:
    start, end = resolve_time_range(start, end)
    rows = await get_top_traces(db, start, end, sort_by, limit, org_id)

    return TopTracesResponse(
        traces=[
            TraceInfo(
                trace_id=row["trace_id"],
                total_cost_usd=float(row["total_cost_usd"] or 0),
                total_tokens=int(row["total_tokens"] or 0),
                total_latency_ms=float(row["total_latency_ms"] or 0),
                event_count=int(row["event_count"] or 0),
                models_used=row["models_used"] or [],
                first_event_at=row["first_event_at"],
                last_event_at=row["last_event_at"],
                agent_framework=row.get("agent_framework"),
            )
            for row in rows
        ],
    )


@router.get("/waste-score", response_model=WasteScoreResponse)
async def waste_score(
    start: datetime | None = None,
    end: datetime | None = None,
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> WasteScoreResponse:
    """Heuristic waste score: what fraction of spend could be saved by using cheaper models.

    Backward-compatible with the v0 response schema. Uses the v0
    heuristic scorer so existing dashboard consumers continue to work.
    The detailed analyzer is available via /waste/details.
    """
    start, end = resolve_time_range(start, end)
    rows = await get_waste_analysis(db, start, end, org_id)
    return compute_waste_score(rows)


@router.get("/waste/details", response_model=WasteReportResponse)
async def waste_details(
    start: datetime | None = None,
    end: datetime | None = None,
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> WasteReportResponse:
    """Full waste analysis with per-detector breakdown.

    Runs all five waste detectors (model overkill, redundant calls,
    context bloat, cache misses, agent loops) and returns a unified
    report with dollar amounts and actionable recommendations.
    """
    start, end = resolve_time_range(start, end)
    analyzer = WasteAnalyzer()
    report = await analyzer.analyze(db, start, end, org_id=org_id)

    return WasteReportResponse(
        items=[
            WasteDetailItem(
                category=item.category.value,
                severity=item.severity.value,
                affected_trace_ids=item.affected_trace_ids,
                call_count=item.call_count,
                current_cost=item.current_cost,
                projected_cost=item.projected_cost,
                savings=item.savings,
                description=item.description,
                confidence=item.confidence,
            )
            for item in report.items
        ],
        total_savings=report.total_savings,
        total_spend=report.total_spend,
        waste_score=report.waste_score,
        generated_at=report.generated_at.isoformat() if report.generated_at else None,
    )
