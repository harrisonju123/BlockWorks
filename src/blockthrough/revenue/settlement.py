"""Settlement engine for processing revenue shares through state channels.

Processes each participant's share by creating payments on their
state channel, then produces a settlement record with a content
hash for on-chain attestation. Earnings are tracked in-memory
for local dev; DB persistence is planned for a future initiative.
"""

from __future__ import annotations

import uuid

from blockthrough.channels.manager import ChannelError, ChannelManager
from blockthrough.utils import utcnow
from blockthrough.pipeline.hasher import hash_content
from blockthrough.revenue.types import (
    ProtocolFee,
    RevenueConfig,
    RevenueShare,
    Settlement,
)


class SettlementError(Exception):
    """Raised when settlement processing fails."""


class SettlementEngine:
    """In-memory settlement processor.

    Tracks settlements and cumulative earnings per participant.
    Uses ChannelManager for payment distribution when channels
    are available, otherwise records settlements without payment.
    """

    def __init__(
        self,
        channel_manager: ChannelManager | None = None,
        config: RevenueConfig | None = None,
    ) -> None:
        self._channel_manager = channel_manager
        self._config = config or RevenueConfig()
        self._settlements: dict[str, Settlement] = {}
        # participant_id -> cumulative USD earned
        self._earnings: dict[str, float] = {}

    def settle(
        self,
        execution_id: str,
        shares: list[RevenueShare],
        protocol_fee: ProtocolFee,
        total_amount: float,
    ) -> Settlement:
        """Process a set of revenue shares into a settlement.

        For each share, attempts to route payment through the
        ChannelManager if one is configured and a channel exists.
        Falls back to bookkeeping-only settlement when channels
        are unavailable — this is the normal local-dev path.

        Args:
            execution_id: The workflow execution being settled.
            shares: Pre-calculated revenue shares from calculate_shares().
            protocol_fee: The protocol fee for this execution.
            total_amount: The original execution cost.

        Returns:
            A Settlement record with all shares marked settled and
            a SHA-256 hash of the settlement data.
        """
        if total_amount < self._config.min_settlement:
            raise SettlementError(
                f"Total amount {total_amount} below minimum settlement "
                f"threshold {self._config.min_settlement}"
            )

        settled_shares: list[RevenueShare] = []

        for share in shares:
            paid = self._try_channel_payment(share)
            settled_share = share.model_copy(update={"settled": paid})
            settled_shares.append(settled_share)

            # Track cumulative earnings regardless of channel payment
            self._earnings[share.participant_id] = (
                self._earnings.get(share.participant_id, 0.0) + share.amount_usd
            )

        settlement_id = str(uuid.uuid4())
        now = utcnow()

        # Hash the settlement for attestation — deterministic over all shares
        hash_payload = {
            "execution_id": execution_id,
            "shares": [
                {
                    "participant_id": s.participant_id,
                    "amount_usd": s.amount_usd,
                    "share_pct": s.share_pct,
                }
                for s in settled_shares
            ],
            "protocol_fee": {
                "fee_amount": protocol_fee.fee_amount,
                "burn_amount": protocol_fee.burn_amount,
            },
            "total_amount": total_amount,
        }
        settlement_hash = hash_content(hash_payload)

        settlement = Settlement(
            id=settlement_id,
            execution_id=execution_id,
            shares=settled_shares,
            protocol_fee=protocol_fee,
            total_amount=total_amount,
            settled_at=now,
            settlement_hash=settlement_hash,
        )

        self._settlements[settlement_id] = settlement
        return settlement

    def get_settlement(self, settlement_id: str) -> Settlement | None:
        """Look up a settlement by its ID."""
        return self._settlements.get(settlement_id)

    def get_earnings(self, participant_id: str) -> float:
        """Get cumulative earnings for a participant in USD."""
        return self._earnings.get(participant_id, 0.0)

    def get_all_earnings(self) -> dict[str, float]:
        """Get earnings for all participants."""
        return dict(self._earnings)

    def get_protocol_stats(self) -> dict:
        """Aggregate protocol-level stats across all settlements."""
        total_fees = 0.0
        total_burned = 0.0
        total_settled = 0.0
        count = 0

        for s in self._settlements.values():
            total_fees += s.protocol_fee.fee_amount
            total_burned += s.protocol_fee.burn_amount
            total_settled += s.total_amount
            count += 1

        return {
            "total_settlements": count,
            "total_fees_collected": round(total_fees, 8),
            "total_burned": round(total_burned, 8),
            "total_volume": round(total_settled, 8),
        }

    def _try_channel_payment(self, share: RevenueShare) -> bool:
        """Attempt to pay a share through a state channel.

        Returns True if payment was made (or bookkeeping-only mode),
        False if channel payment failed.
        """
        if self._channel_manager is None:
            # No channel manager — bookkeeping-only settlement
            return True

        if share.amount_usd <= 0:
            # Nothing to pay
            return True

        # Find an open channel where the participant is the receiver
        channels = self._channel_manager.get_channels_for(share.participant_id)
        receiver_channels = [
            ch for ch in channels if ch.receiver == share.participant_id
        ]

        if not receiver_channels:
            # No channel available — still record the settlement
            return False

        # Use the first available channel with sufficient remaining deposit
        for ch in receiver_channels:
            remaining = ch.deposit_amount - ch.spent_amount
            if remaining >= share.amount_usd:
                try:
                    self._channel_manager.create_payment(
                        ch.channel_id, share.amount_usd
                    )
                    return True
                except ChannelError:
                    continue

        return False
