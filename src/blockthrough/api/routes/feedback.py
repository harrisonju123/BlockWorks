"""Explicit feedback API endpoint."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from blockthrough.feedback.types import FeedbackSignal, SIGNAL_DEFAULTS
from blockthrough.utils import utcnow

router = APIRouter()


class FeedbackRequest(BaseModel):
    event_id: str
    rating: Literal["positive", "negative"]
    comment: str | None = None


@router.post("/feedback", status_code=201)
async def submit_feedback(payload: FeedbackRequest):
    """Accept explicit user feedback on a routing decision."""
    from blockthrough.api.deps import get_async_session
    from sqlalchemy import text

    # Validate event exists and get model + task_type
    async with get_async_session() as session:
        row = await session.execute(
            text("SELECT model, task_type FROM llm_events WHERE id = :eid LIMIT 1"),
            {"eid": payload.event_id},
        )
        event = row.mappings().first()
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")

        signal = (
            FeedbackSignal.EXPLICIT_POSITIVE
            if payload.rating == "positive"
            else FeedbackSignal.EXPLICIT_NEGATIVE
        )
        delta, weight = SIGNAL_DEFAULTS[signal]
        now = utcnow()

        await session.execute(
            text("""
                INSERT INTO feedback_signals (id, created_at, event_id, model, task_type, signal, quality_delta, weight, source)
                VALUES (:id, :created_at, :event_id, :model, :task_type, :signal, :delta, :weight, 'explicit')
                ON CONFLICT (event_id, signal, created_at) DO NOTHING
            """),
            {
                "id": str(uuid.uuid4()),
                "created_at": now,
                "event_id": payload.event_id,
                "model": event["model"],
                "task_type": event["task_type"] or "unknown",
                "signal": signal.value,
                "delta": delta,
                "weight": weight,
            },
        )
        await session.commit()

    return {"status": "recorded", "signal": signal.value}
