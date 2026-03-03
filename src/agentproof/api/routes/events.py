"""Event listing and detail endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.api.deps import get_db, resolve_time_range
from agentproof.api.schemas import EventDetail, EventsResponse
from agentproof.types import EventStatus, TaskType

router = APIRouter()


def _add_filter(
    filters: list[str], params: dict, name: str, value: object
) -> None:
    """Append a SQL equality filter if value is not None."""
    if value is not None:
        val = value.value if isinstance(value, (EventStatus, TaskType)) else value
        filters.append(f"{name} = :{name}")
        params[name] = val


@router.get("/events", response_model=EventsResponse)
async def list_events(
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    provider: str | None = None,
    task_type: TaskType | None = None,
    status: EventStatus | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    org_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> EventsResponse:
    start, end = resolve_time_range(start, end)

    filters = ["created_at >= :start", "created_at < :end"]
    params: dict = {"start": start, "end": end, "limit": limit, "offset": offset}

    _add_filter(filters, params, "model", model)
    _add_filter(filters, params, "provider", provider)
    _add_filter(filters, params, "task_type", task_type)
    _add_filter(filters, params, "status", status)
    _add_filter(filters, params, "trace_id", trace_id)
    _add_filter(filters, params, "session_id", session_id)
    _add_filter(filters, params, "org_id", org_id)

    where = " AND ".join(filters)

    # Use window function to get count in a single query
    query = text(f"""
        SELECT *, COUNT(*) OVER() AS _total_count
        FROM llm_events
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await db.execute(query, params)
    rows = result.fetchall()

    total_count = int(rows[0]._mapping["_total_count"]) if rows else 0

    events = [
        EventDetail(
            id=str(row._mapping["id"]),
            created_at=row._mapping["created_at"],
            status=row._mapping["status"],
            provider=row._mapping["provider"],
            model=row._mapping["model"],
            prompt_tokens=row._mapping["prompt_tokens"],
            completion_tokens=row._mapping["completion_tokens"],
            total_tokens=row._mapping["total_tokens"],
            estimated_cost=row._mapping["estimated_cost"],
            latency_ms=row._mapping["latency_ms"],
            trace_id=row._mapping["trace_id"],
            span_id=row._mapping["span_id"],
            task_type=row._mapping["task_type"],
            task_type_confidence=row._mapping["task_type_confidence"],
            has_tool_calls=row._mapping["has_tool_calls"],
            agent_framework=row._mapping["agent_framework"],
        )
        for row in rows
    ]

    return EventsResponse(
        events=events,
        total_count=total_count,
        has_more=(offset + limit) < total_count,
    )
