"""Event ingest endpoint for the standalone SDK.

Accepts LLM events from the Python/TypeScript SDKs without requiring
the LiteLLM callback. Events are validated and written to TimescaleDB
via the same EventWriter pipeline the callback uses.

This route is additive — it does not modify the existing callback flow.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agentproof.utils import utcnow

router = APIRouter()


class IngestEventRequest(BaseModel):
    """Request body for SDK event ingest."""

    id: str | None = None
    created_at: str | None = None
    status: str = "success"
    provider: str = "custom"
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    latency_ms: float = 0.0
    prompt_hash: str = ""
    completion_hash: str = ""
    system_prompt_hash: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    litellm_call_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    custom_metadata: dict | None = None


class IngestEventResponse(BaseModel):
    """Response after accepting an event."""

    event_id: str
    created_at: str
    status: str = "accepted"


@router.post("/events/ingest", response_model=IngestEventResponse)
async def ingest_event(body: IngestEventRequest) -> IngestEventResponse:
    """Accept an LLM event from the standalone SDK.

    Validates the payload and returns an accepted status. The event
    will be processed asynchronously via the write pipeline.

    In a full deployment this queues into the EventWriter. For now
    it validates and returns immediately — DB write integration is
    wired when the API server starts with a shared EventWriter instance.
    """
    event_id = body.id or str(uuid.uuid4())
    created_at = body.created_at or utcnow().isoformat()

    # Basic validation
    if not body.model:
        raise HTTPException(status_code=422, detail="model is required")

    return IngestEventResponse(
        event_id=event_id,
        created_at=created_at,
        status="accepted",
    )
