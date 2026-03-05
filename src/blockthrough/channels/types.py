"""Pydantic models for the state channel subsystem.

These types represent the off-chain state of payment channels between
agent operators (senders) and tool/MCP providers (receivers). All
monetary amounts are in USD (or ETH-equivalent for on-chain settlement).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChannelState(BaseModel):
    """Current state of a payment channel.

    Tracks the deposit, cumulative spend, and nonce for off-chain
    payment updates between a sender and receiver pair.
    """

    channel_id: str
    sender: str
    receiver: str
    deposit_amount: float
    spent_amount: float = 0.0
    nonce: int = 0
    is_open: bool = True
    opened_at: datetime
    last_updated: datetime


class PaymentUpdate(BaseModel):
    """A single off-chain payment increment.

    Each update increments the nonce and records the cumulative amount
    paid so far (not the delta). The sender signs each update; the
    receiver can optionally co-sign for cooperative close.
    """

    channel_id: str
    amount: float
    nonce: int
    sender_signature: str
    receiver_signature: str | None = None


class ChannelConfig(BaseModel):
    """Tunable parameters for channel behavior."""

    max_channel_duration_s: int = 3600
    min_deposit: float = 0.01
    settlement_delay_s: int = 300


class SettlementProof(BaseModel):
    """Final proof submitted for on-chain settlement.

    Contains the last agreed-upon state (nonce + amount) with both
    parties' signatures, enabling the contract to distribute funds.
    """

    channel_id: str
    final_nonce: int
    final_amount: float
    sender_sig: str
    receiver_sig: str
