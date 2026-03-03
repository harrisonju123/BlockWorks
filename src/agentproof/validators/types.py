"""Pydantic models for the decentralized benchmark validation subsystem.

These types define the contract between the validator registry, task
distribution, consensus engine, economics module, and the API layer.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ValidatorStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SLASHED = "slashed"


class ValidatorInfo(BaseModel):
    """A registered validator node that stakes tokens to participate."""

    address: str
    stake_amount: float = Field(ge=0.0)
    registered_at: datetime
    is_active: bool = True
    total_validations: int = 0
    accuracy_score: float = Field(ge=0.0, le=1.0, default=1.0)
    cumulative_rewards: float = 0.0
    cumulative_slashes: float = 0.0


class ValidationTask(BaseModel):
    """A benchmark validation task distributed to validators."""

    task_id: str
    benchmark_model: str
    task_type: str
    prompt_hash: str
    original_completion_hash: str
    created_at: datetime
    deadline: datetime
    assigned_validators: list[str] = Field(default_factory=list)


class ValidationSubmission(BaseModel):
    """A validator's quality score submission for a task."""

    task_id: str
    validator_address: str
    quality_score: float = Field(ge=0.0, le=1.0)
    judge_model: str = "claude-haiku-4-5-20251001"
    submitted_at: datetime
    signature: str


class ConsensusResult(BaseModel):
    """Outcome of consensus on a validation task."""

    task_id: str
    agreed_score: float | None = None
    submissions: list[ValidationSubmission] = Field(default_factory=list)
    consensus_reached: bool = False
    agreement_threshold: int = 2
