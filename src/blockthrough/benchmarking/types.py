"""Pydantic models for the benchmarking subsystem.

These types are the contract between the traffic mirror, the LLM-as-judge,
the fitness matrix query layer, and the API. Changes here require coordinated
updates across all four surfaces.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from blockthrough.types import TaskType


class RubricCriterion(BaseModel):
    """A single scoring dimension within a rubric."""

    name: str
    weight: float = Field(ge=0.0, le=1.0)
    prompt: str


class Rubric(BaseModel):
    """Task-specific scoring rubric used by the LLM-as-judge."""

    task_type: TaskType
    version: str = "1.0"
    criteria: list[RubricCriterion]


class BenchmarkResult(BaseModel):
    """Output of a single benchmark evaluation (one prompt x one alternative model)."""

    id: UUID
    created_at: datetime
    original_event_id: UUID
    original_model: str
    benchmark_model: str
    task_type: TaskType
    quality_score: float = Field(ge=0.0, le=1.0)
    original_cost: float
    benchmark_cost: float
    original_latency_ms: float
    benchmark_latency_ms: float
    judge_model: str = "claude-sonnet-4-6"
    rubric_version: str
    org_id: str | None = None


class BenchmarkConfig(BaseModel):
    """Runtime configuration controlling what gets benchmarked and how."""

    sample_rate: float = Field(ge=0.0, le=1.0, default=0.05)
    benchmark_models: list[str] = Field(
        default_factory=lambda: ["claude-haiku-4-5-20251001", "gpt-4o-mini"]
    )
    enabled_task_types: list[TaskType] = Field(
        default_factory=lambda: list(TaskType)
    )
    judge_model: str = "claude-sonnet-4-6"
    api_base: str | None = None


class FitnessEntry(BaseModel):
    """One cell of the fitness matrix: how a model performs on a task type."""

    task_type: str
    model: str
    avg_quality: float
    avg_cost: float
    avg_latency: float
    sample_size: int
