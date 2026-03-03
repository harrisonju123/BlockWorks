"""Pydantic models for the Global Fitness Index.

These types define the contract between the leaderboard builder,
comparison engine, trend analysis, widget generator, and the API layer.
Reuses FitnessEntry from benchmarking as the raw data source.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LeaderboardEntry(BaseModel):
    """A ranked model's performance on a specific task type."""

    model: str
    task_type: str
    quality_score: float = Field(ge=0.0, le=1.0)
    cost_per_1k: float = Field(ge=0.0)
    latency_ms: float = Field(ge=0.0)
    sample_size: int = Field(ge=0)
    rank: int = Field(ge=1)
    verified: bool = False
    last_updated: datetime | None = None


class LeaderboardFilter(BaseModel):
    """Query filters for the leaderboard endpoint."""

    task_type: str | None = None
    min_sample_size: int = 10
    verified_only: bool = False


class ModelComparison(BaseModel):
    """Head-to-head comparison of two models on a task type."""

    model_a: str
    model_b: str
    task_type: str
    quality_delta: float
    cost_delta: float
    latency_delta: float
    recommendation: str


class TrendPoint(BaseModel):
    """A single data point in a model's performance trend line."""

    timestamp: datetime
    model: str
    task_type: str
    quality_score: float
    cost: float
    sample_size: int


class FitnessIndexConfig(BaseModel):
    """Tuning knobs for the fitness index builder."""

    refresh_interval_s: int = 300
    min_sample_size: int = 10
    trend_window_days: int = 30
