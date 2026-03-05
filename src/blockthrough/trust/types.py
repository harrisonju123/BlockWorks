"""Pydantic models for the trust score subsystem.

Trust scores are multi-dimensional reputation signals: reliability, efficiency,
quality, and usage. Each dimension is a float in [0, 1]. The composite score
is a weighted sum. On-chain, these map to uint16 (0-10000).
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class TrustDimension(str, enum.Enum):
    RELIABILITY = "reliability"
    EFFICIENCY = "efficiency"
    QUALITY = "quality"
    USAGE = "usage"


class TrustScore(BaseModel):
    """Current trust score for an agent."""

    agent_id: str
    reliability: float = Field(ge=0.0, le=1.0)
    efficiency: float = Field(ge=0.0, le=1.0)
    quality: float = Field(ge=0.0, le=1.0)
    usage_volume: float = Field(ge=0.0, le=1.0)
    composite_score: float = Field(ge=0.0, le=1.0)
    last_updated: datetime


class TrustWeights(BaseModel):
    """Weights for computing the composite trust score.

    Must sum to 1.0 (enforced by the calculator, not the model, to allow
    configuration flexibility).
    """

    reliability_weight: float = Field(default=0.30, ge=0.0, le=1.0)
    efficiency_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    quality_weight: float = Field(default=0.30, ge=0.0, le=1.0)
    usage_weight: float = Field(default=0.15, ge=0.0, le=1.0)


class ScoreUpdate(BaseModel):
    """A record of a single dimension update for audit/history."""

    agent_id: str
    dimension: TrustDimension
    old_value: float
    new_value: float
    reason: str
    timestamp: datetime
