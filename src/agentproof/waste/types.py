"""Pydantic models and enums for the waste detection subsystem."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class WasteCategory(str, enum.Enum):
    MODEL_OVERKILL = "model_overkill"
    REDUNDANT_CALLS = "redundant_calls"
    CONTEXT_BLOAT = "context_bloat"
    CACHE_MISSES = "cache_misses"
    AGENT_LOOPS = "agent_loops"


class WasteSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class WasteItem(BaseModel):
    """A single waste finding from a detector."""

    category: WasteCategory
    severity: WasteSeverity
    affected_trace_ids: list[str] = Field(default_factory=list)
    call_count: int = 0
    current_cost: float = 0.0
    projected_cost: float = 0.0
    savings: float = 0.0
    description: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class WasteReport(BaseModel):
    """Aggregated output from all waste detectors."""

    items: list[WasteItem] = Field(default_factory=list)
    total_savings: float = 0.0
    total_spend: float = 0.0
    waste_score: float = Field(ge=0.0, le=1.0, default=0.0)
    generated_at: datetime | None = None
