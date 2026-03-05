"""Pydantic request/response models for the REST API."""

from datetime import datetime

from pydantic import BaseModel

from blockthrough.types import EventStatus, TaskType


class Period(BaseModel):
    start: datetime
    end: datetime


class StatGroup(BaseModel):
    key: str
    request_count: int
    total_cost_usd: float
    avg_latency_ms: float
    p95_latency_ms: float
    avg_cost_per_request_usd: float
    total_prompt_tokens: int
    total_completion_tokens: int
    failure_count: int


class SummaryResponse(BaseModel):
    period: Period
    total_requests: int
    total_cost_usd: float
    total_tokens: int
    failure_rate: float
    groups: list[StatGroup]


class TimeseriesPoint(BaseModel):
    timestamp: datetime
    value: float


class TimeseriesResponse(BaseModel):
    metric: str
    interval: str
    data: list[TimeseriesPoint]


class TraceInfo(BaseModel):
    trace_id: str
    total_cost_usd: float
    total_tokens: int
    total_latency_ms: float
    event_count: int
    models_used: list[str]
    first_event_at: datetime
    last_event_at: datetime
    agent_framework: str | None


class TopTracesResponse(BaseModel):
    traces: list[TraceInfo]


class WasteBreakdownItem(BaseModel):
    task_type: TaskType
    current_model: str
    suggested_model: str
    call_count: int
    current_cost_usd: float
    projected_cost_usd: float
    savings_usd: float
    confidence: float
    suggestion_source: str | None = None
    quality_score: float | None = None
    sample_size: int | None = None


class WasteScoreResponse(BaseModel):
    waste_score: float
    total_potential_savings_usd: float
    breakdown: list[WasteBreakdownItem]


class EventDetail(BaseModel):
    id: str
    created_at: datetime
    status: EventStatus
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float
    latency_ms: float
    trace_id: str
    span_id: str
    task_type: TaskType | None
    task_type_confidence: float | None
    has_tool_calls: bool
    agent_framework: str | None


class EventsResponse(BaseModel):
    events: list[EventDetail]
    total_count: int
    has_more: bool


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


class WasteDetailItem(BaseModel):
    """Individual waste finding in the detailed report."""

    category: str
    severity: str
    affected_trace_ids: list[str]
    call_count: int
    current_cost: float
    projected_cost: float
    savings: float
    description: str
    confidence: float


class WasteReportResponse(BaseModel):
    """Full waste report with per-item breakdown."""

    items: list[WasteDetailItem]
    total_savings: float
    total_spend: float
    waste_score: float
    generated_at: str | None = None
