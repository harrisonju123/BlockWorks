"""SDK-specific types and request/response models.

These are the public-facing types users interact with when using the
standalone Python SDK. They're intentionally decoupled from the internal
pipeline types so the SDK can evolve independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SDKConfig:
    """Configuration for the AgentProof SDK client."""

    api_url: str = "http://localhost:8100"
    api_key: str | None = None
    timeout_s: float = 30.0
    max_retries: int = 3


@dataclass
class TrackEventRequest:
    """Payload sent to the API when tracking an LLM call."""

    model: str
    messages: list[dict]
    completion: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float
    latency_ms: float
    provider: str = "custom"
    status: str = "success"
    session_id: str | None = None
    trace_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class TrackEventResponse:
    """Response after successfully tracking an event."""

    event_id: str
    created_at: str


@dataclass(frozen=True)
class StatGroup:
    key: str
    request_count: int
    total_cost_usd: float
    avg_latency_ms: float
    p95_latency_ms: float
    avg_cost_per_request_usd: float
    total_prompt_tokens: int
    total_completion_tokens: int
    failure_count: int


@dataclass(frozen=True)
class StatsResponse:
    """Summary stats returned by the API."""

    total_requests: int
    total_cost_usd: float
    total_tokens: int
    failure_rate: float
    groups: list[StatGroup]


@dataclass(frozen=True)
class WasteBreakdownItem:
    task_type: str
    current_model: str
    suggested_model: str
    call_count: int
    current_cost_usd: float
    projected_cost_usd: float
    savings_usd: float
    confidence: float


@dataclass(frozen=True)
class WasteScoreResponse:
    """Waste score with per-task breakdown."""

    waste_score: float
    total_potential_savings_usd: float
    breakdown: list[WasteBreakdownItem]


@dataclass(frozen=True)
class FitnessEntry:
    model: str
    task_type: str
    quality_score: float
    avg_cost: float
    avg_latency_ms: float
    sample_count: int


@dataclass(frozen=True)
class FitnessMatrixResponse:
    """Fitness matrix: per (model, task_type) performance data."""

    entries: list[FitnessEntry]


@dataclass(frozen=True)
class CostEstimate:
    """Estimated cost impact from analyzing a PR diff."""

    new_llm_calls_found: int
    estimated_monthly_cost: float
    estimated_monthly_tokens: int
    details: list[CostEstimateDetail]
    summary: str


@dataclass(frozen=True)
class CostEstimateDetail:
    """One LLM call site discovered in a diff."""

    file_path: str
    line_number: int
    model_hint: str | None
    call_type: str
    estimated_calls_per_month: int
    estimated_cost_per_call: float
    estimated_monthly_cost: float


@dataclass
class TraceContext:
    """Context manager state for grouping calls into traces."""

    session_id: str
    trace_id: str
    events: list[str] = field(default_factory=list)
    started_at: datetime | None = None
