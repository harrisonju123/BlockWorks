"""Types for the user feedback subsystem."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackSignal(str, enum.Enum):
    RETRY = "retry"
    OVERRIDE = "override"
    ABANDON = "abandon"
    EXPLICIT_POSITIVE = "explicit_positive"
    EXPLICIT_NEGATIVE = "explicit_negative"


# Default quality_delta and weight per signal type
SIGNAL_DEFAULTS: dict[FeedbackSignal, tuple[float, float]] = {
    FeedbackSignal.RETRY: (-0.10, 1.0),
    FeedbackSignal.OVERRIDE: (-0.15, 1.2),
    FeedbackSignal.ABANDON: (-0.05, 0.5),
    FeedbackSignal.EXPLICIT_POSITIVE: (0.05, 0.8),
    FeedbackSignal.EXPLICIT_NEGATIVE: (-0.10, 1.0),
}


class FeedbackRecord(BaseModel):
    """A single feedback signal about an LLM event."""
    id: UUID
    created_at: datetime
    event_id: UUID
    model: str
    task_type: str
    signal: FeedbackSignal
    quality_delta: float
    weight: float = 1.0
    source: str = "implicit"
