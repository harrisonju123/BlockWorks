"""MCP tracing endpoints: per-server stats, execution graph, waste detection."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from blockthrough.api.deps import get_db, resolve_time_range
from blockthrough.db.queries import (
    get_mcp_execution_graph,
    get_mcp_server_stats,
    get_mcp_unused_data,
)
from blockthrough.mcp.types import MCPServerStats

router = APIRouter(prefix="/mcp")


# --- Response schemas ---


class MCPServerStatsResponse(BaseModel):
    stats: list[MCPServerStats]


class MCPGraphNode(BaseModel):
    id: str
    event_id: str
    created_at: datetime
    server_name: str
    method: str
    params_hash: str
    response_hash: str | None
    latency_ms: float | None
    response_tokens: int | None
    status: str
    error_type: str | None


class MCPGraphEdge(BaseModel):
    id: str
    parent_call_id: str
    child_call_id: str
    trace_id: str


class MCPGraphResponse(BaseModel):
    trace_id: str
    nodes: list[MCPGraphNode]
    edges: list[MCPGraphEdge]


class MCPWasteItem(BaseModel):
    server_name: str
    method: str
    unused_call_count: int
    total_wasted_tokens: int
    avg_wasted_tokens: float


class MCPWasteResponse(BaseModel):
    waste: list[MCPWasteItem]


# --- Endpoints ---


@router.get("/stats", response_model=MCPServerStatsResponse)
async def mcp_stats(
    start: datetime | None = None,
    end: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> MCPServerStatsResponse:
    """Per-server P50/P95 latency, failure rate, call count."""
    start, end = resolve_time_range(start, end)
    rows = await get_mcp_server_stats(db, start, end)
    return MCPServerStatsResponse(
        stats=[
            MCPServerStats(
                server_name=row["server_name"],
                call_count=int(row["call_count"]),
                failure_count=int(row["failure_count"]),
                failure_rate=float(row["failure_rate"] or 0),
                avg_latency_ms=float(row["avg_latency_ms"]) if row["avg_latency_ms"] else None,
                p50_latency_ms=float(row["p50_latency_ms"]) if row["p50_latency_ms"] else None,
                p95_latency_ms=float(row["p95_latency_ms"]) if row["p95_latency_ms"] else None,
            )
            for row in rows
        ]
    )


@router.get("/graph/{trace_id}", response_model=MCPGraphResponse)
async def mcp_graph(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
) -> MCPGraphResponse:
    """Full execution DAG for a trace."""
    data = await get_mcp_execution_graph(db, trace_id)
    return MCPGraphResponse(
        trace_id=data["trace_id"],
        nodes=[
            MCPGraphNode(
                id=str(n["id"]),
                event_id=str(n["event_id"]),
                created_at=n["created_at"],
                server_name=n["server_name"],
                method=n["method"],
                params_hash=n["params_hash"],
                response_hash=n.get("response_hash"),
                latency_ms=float(n["latency_ms"]) if n.get("latency_ms") else None,
                response_tokens=int(n["response_tokens"]) if n.get("response_tokens") else None,
                status=n["status"],
                error_type=n.get("error_type"),
            )
            for n in data["nodes"]
        ],
        edges=[
            MCPGraphEdge(
                id=str(e["id"]),
                parent_call_id=str(e["parent_call_id"]),
                child_call_id=str(e["child_call_id"]),
                trace_id=e["trace_id"],
            )
            for e in data["edges"]
        ],
    )


@router.get("/waste", response_model=MCPWasteResponse)
async def mcp_waste(
    start: datetime | None = None,
    end: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> MCPWasteResponse:
    """Unused MCP data analysis -- MCP responses that consumed context tokens but were never referenced."""
    start, end = resolve_time_range(start, end)
    rows = await get_mcp_unused_data(db, start, end)
    return MCPWasteResponse(
        waste=[
            MCPWasteItem(
                server_name=row["server_name"],
                method=row["method"],
                unused_call_count=int(row["unused_call_count"]),
                total_wasted_tokens=int(row["total_wasted_tokens"] or 0),
                avg_wasted_tokens=float(row["avg_wasted_tokens"] or 0),
            )
            for row in rows
        ]
    )
