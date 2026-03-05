"""Pydantic models for MCP tracing data.

MCPCall mirrors the mcp_calls DB table. MCPExecutionEdge represents a
parent->child relationship in the execution DAG. MCPServerStats holds
aggregated per-server metrics for the analytics API.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from blockthrough.types import EventStatus


class MCPCall(BaseModel):
    """A single MCP tool invocation observed in an LLM response."""

    id: UUID
    created_at: datetime
    event_id: UUID
    trace_id: str
    server_name: str
    method: str
    params_hash: str
    response_hash: str | None = None
    latency_ms: float | None = None
    response_tokens: int | None = None
    status: EventStatus = EventStatus.SUCCESS
    error_type: str | None = None


class MCPExecutionEdge(BaseModel):
    """A directed edge in the MCP execution DAG.

    Represents that `parent_call_id` produced output consumed
    before `child_call_id` was invoked within the same trace.
    """

    id: UUID
    parent_call_id: UUID
    child_call_id: UUID
    trace_id: str


class MCPServerStats(BaseModel):
    """Aggregated per-server metrics returned by the analytics API."""

    server_name: str
    call_count: int
    failure_count: int
    failure_rate: float = Field(ge=0.0, le=1.0)
    avg_latency_ms: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
