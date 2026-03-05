"""Pydantic models for the governance subsystem.

These types define the proposal lifecycle: creation, voting, and tallying.
Governance is token-weighted — vote weight equals the voter's token balance
at the time of casting.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PASSED = "passed"
    REJECTED = "rejected"
    EXECUTED = "executed"


class VoteSupport(str, enum.Enum):
    FOR = "for"
    AGAINST = "against"


class Proposal(BaseModel):
    """A governance proposal with voting state."""

    id: str
    title: str
    description: str
    proposer: str
    created_at: datetime
    voting_deadline: datetime
    for_votes: int = 0
    against_votes: int = 0
    status: ProposalStatus = ProposalStatus.ACTIVE


class Vote(BaseModel):
    """A single vote cast on a proposal."""

    proposal_id: str
    voter: str
    weight: int = Field(ge=0, description="Token balance at time of voting")
    support: VoteSupport


class GovernanceConfig(BaseModel):
    """Runtime config for governance parameters."""

    voting_period_s: int = 604_800  # 7 days
    quorum_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    proposal_threshold: int = Field(
        default=0,
        ge=0,
        description="Minimum token balance to create a proposal",
    )
    total_supply: int = Field(
        default=1_000_000_000,
        description="Total token supply for quorum calculation",
    )
