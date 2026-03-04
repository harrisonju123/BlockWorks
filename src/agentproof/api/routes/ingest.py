"""Event ingest endpoint for the standalone SDK.

Accepts LLM events from the Python/TypeScript SDKs without requiring
the LiteLLM callback. Events are validated and written to TimescaleDB
via the same EventWriter pipeline the callback uses.

This route is additive — it does not modify the existing callback flow.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agentproof.pipeline.hasher import hash_content
from agentproof.types import EventStatus, LLMEvent
from agentproof.utils import utcnow

logger = logging.getLogger(__name__)
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
async def ingest_event(body: IngestEventRequest, request: Request) -> IngestEventResponse:
    """Accept an LLM event from the standalone SDK.

    Validates the payload, converts to LLMEvent, and enqueues for
    the EventWriter pipeline (same path as the proxy/callback flow).
    """
    if not body.model:
        raise HTTPException(status_code=422, detail="model is required")

    try:
        event_id = _uuid.UUID(body.id) if body.id else _uuid.uuid4()
    except ValueError:
        raise HTTPException(status_code=422, detail="id must be a valid UUID")

    try:
        status = EventStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"status must be 'success' or 'failure', got '{body.status}'")

    now = utcnow()
    try:
        created_at = datetime.fromisoformat(body.created_at) if body.created_at else now
    except ValueError:
        raise HTTPException(status_code=422, detail="created_at must be a valid ISO 8601 timestamp")

    event = LLMEvent(
        id=event_id,
        created_at=created_at,
        status=status,
        provider=body.provider,
        model=body.model,
        prompt_tokens=body.prompt_tokens,
        completion_tokens=body.completion_tokens,
        total_tokens=body.total_tokens or (body.prompt_tokens + body.completion_tokens),
        estimated_cost=body.estimated_cost,
        latency_ms=body.latency_ms,
        prompt_hash=body.prompt_hash or hash_content(""),
        completion_hash=body.completion_hash or hash_content(""),
        system_prompt_hash=body.system_prompt_hash,
        session_id=body.session_id,
        trace_id=body.trace_id or str(_uuid.uuid4()),
        span_id=body.span_id or _uuid.uuid4().hex[:16],
        litellm_call_id=body.litellm_call_id or str(_uuid.uuid4()),
        org_id=body.org_id,
        user_id=body.user_id,
        custom_metadata=body.custom_metadata,
    )

    queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("Event queue full, rejecting ingest for event %s", event_id)
        raise HTTPException(status_code=503, detail="Event queue full, try again later")

    return IngestEventResponse(
        event_id=str(event_id),
        created_at=created_at.isoformat(),
        status="accepted",
    )
