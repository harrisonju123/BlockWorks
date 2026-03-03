"""Pydantic models for the revenue sharing subsystem.

Defines the data structures for revenue splits, settlement records,
protocol fees, and configuration. All monetary amounts are in USD.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class SplitBasis(str, enum.Enum):
    """How a participant's share is determined.

    Each basis uses a different signal to proportion revenue:
    - token_usage: proportional to tokens consumed
    - exec_time: proportional to wall-clock execution time
    - value_add: weighted by quality/benchmark scores
    - fixed: predetermined percentage (weight = pct in basis points)
    """

    TOKEN_USAGE = "token_usage"
    EXEC_TIME = "exec_time"
    VALUE_ADD = "value_add"
    FIXED = "fixed"


class SplitRule(BaseModel):
    """Defines how one participant's share is calculated."""

    participant_id: str
    basis: SplitBasis
    weight: float = Field(ge=0.0)


class RevenueShare(BaseModel):
    """A single participant's share of a workflow execution's revenue."""

    workflow_execution_id: str
    participant_id: str
    share_pct: float = Field(ge=0.0, le=100.0)
    amount_usd: float = Field(ge=0.0)
    settled: bool = False


class ProtocolFee(BaseModel):
    """Fee retained by the protocol from a settlement.

    A portion of the fee is burned (sent to a dead address) to create
    deflationary pressure on the token supply.
    """

    execution_id: str
    fee_pct: float = Field(ge=0.0, le=100.0)
    fee_amount: float = Field(ge=0.0)
    burn_amount: float = Field(ge=0.0)


class Settlement(BaseModel):
    """Complete settlement record for one workflow execution."""

    id: str
    execution_id: str
    shares: list[RevenueShare]
    protocol_fee: ProtocolFee
    total_amount: float = Field(ge=0.0)
    settled_at: datetime | None = None
    settlement_hash: str = ""


class RevenueConfig(BaseModel):
    """Tunable parameters for the revenue sharing protocol."""

    protocol_fee_pct: float = Field(default=3.0, ge=0.0, le=100.0)
    burn_pct: float = Field(default=30.0, ge=0.0, le=100.0)
    min_settlement: float = Field(default=0.001, ge=0.0)
