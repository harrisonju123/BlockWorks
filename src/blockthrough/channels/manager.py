"""Core state channel lifecycle management.

In-memory implementation for local dev. Handles channel open, off-chain
payment creation/verification, and cooperative close with settlement
proof generation. DB persistence is deferred to a future initiative.

Invariants enforced:
  - Payments cannot exceed the channel deposit.
  - Nonces are strictly sequential (monotonically increasing).
  - Only open channels accept payments.
  - Close produces a settlement proof with both signatures.
"""

from __future__ import annotations

import uuid

from blockthrough.channels.signing import sign_payment, verify_signature
from blockthrough.utils import utcnow
from blockthrough.channels.types import (
    ChannelConfig,
    ChannelState,
    PaymentUpdate,
    SettlementProof,
)


class ChannelError(Exception):
    """Raised when a channel operation violates an invariant."""


class ChannelManager:
    """In-memory state channel manager.

    Stores channels in a dict keyed by channel_id. Mirrors what a
    Solidity contract would enforce on-chain, so upstream code can
    develop against fast, deterministic local behavior.
    """

    def __init__(self, config: ChannelConfig | None = None) -> None:
        self._config = config or ChannelConfig()
        self._channels: dict[str, ChannelState] = {}
        # sender_key is used for signing — in local dev, same as public key
        self._sender_keys: dict[str, str] = {}

    def open_channel(
        self,
        sender: str,
        receiver: str,
        deposit: float,
        sender_key: str = "default-sender-key",
    ) -> ChannelState:
        """Open a new payment channel with the given deposit locked.

        Returns the initial ChannelState. The deposit sets the upper
        bound for cumulative payments on this channel.
        """
        if deposit < self._config.min_deposit:
            raise ChannelError(
                f"Deposit {deposit} below minimum {self._config.min_deposit}"
            )

        if not sender:
            raise ChannelError("sender must not be empty")
        if not receiver:
            raise ChannelError("receiver must not be empty")

        now = utcnow()
        channel_id = str(uuid.uuid4())

        state = ChannelState(
            channel_id=channel_id,
            sender=sender,
            receiver=receiver,
            deposit_amount=deposit,
            spent_amount=0.0,
            nonce=0,
            is_open=True,
            opened_at=now,
            last_updated=now,
        )

        self._channels[channel_id] = state
        self._sender_keys[channel_id] = sender_key
        return state

    def create_payment(
        self,
        channel_id: str,
        amount: float,
    ) -> PaymentUpdate:
        """Create an off-chain payment update for the given amount.

        The amount is the *incremental* payment, not cumulative.
        Internally we track cumulative spend and sign with that value.
        """
        state = self._get_open_channel(channel_id)

        new_spent = state.spent_amount + amount
        if new_spent > state.deposit_amount:
            raise ChannelError(
                f"Payment of {amount} would exceed deposit: "
                f"spent={state.spent_amount}, deposit={state.deposit_amount}"
            )

        if amount <= 0:
            raise ChannelError(f"Payment amount must be positive, got {amount}")

        new_nonce = state.nonce + 1
        sender_key = self._sender_keys[channel_id]

        signature = sign_payment(channel_id, new_spent, new_nonce, sender_key)

        # Update local state
        state.spent_amount = new_spent
        state.nonce = new_nonce
        state.last_updated = utcnow()

        return PaymentUpdate(
            channel_id=channel_id,
            amount=new_spent,
            nonce=new_nonce,
            sender_signature=signature,
        )

    def receive_payment(self, payment: PaymentUpdate, receiver_key: str) -> bool:
        """Validate and accept an incoming payment update.

        Verifies the sender's signature, checks nonce sequencing, and
        ensures the amount doesn't exceed the deposit. Returns True if
        accepted, raises ChannelError on validation failure.
        """
        state = self._get_open_channel(payment.channel_id)
        sender_key = self._sender_keys[payment.channel_id]

        # Nonce must be exactly one ahead of current state
        if payment.nonce != state.nonce + 1:
            raise ChannelError(
                f"Nonce mismatch: expected {state.nonce + 1}, got {payment.nonce}"
            )

        # Amount must not exceed deposit
        if payment.amount > state.deposit_amount:
            raise ChannelError(
                f"Payment amount {payment.amount} exceeds deposit {state.deposit_amount}"
            )

        # Amount must be non-decreasing (cumulative)
        if payment.amount < state.spent_amount:
            raise ChannelError(
                f"Cumulative amount {payment.amount} less than "
                f"current spent {state.spent_amount}"
            )

        # Verify sender signature
        if not verify_signature(
            payment.channel_id,
            payment.amount,
            payment.nonce,
            payment.sender_signature,
            sender_key,
        ):
            raise ChannelError("Invalid sender signature")

        # Accept the payment — update local state
        state.spent_amount = payment.amount
        state.nonce = payment.nonce
        state.last_updated = utcnow()

        return True

    def close_channel(
        self,
        channel_id: str,
        receiver_key: str = "default-receiver-key",
    ) -> SettlementProof:
        """Close a channel and generate a settlement proof.

        Both sender and receiver sign the final state. The proof can
        be submitted on-chain for fund distribution.
        """
        state = self._get_open_channel(channel_id)
        sender_key = self._sender_keys[channel_id]

        # Sign the final state with both keys
        sender_sig = sign_payment(
            channel_id, state.spent_amount, state.nonce, sender_key
        )
        receiver_sig = sign_payment(
            channel_id, state.spent_amount, state.nonce, receiver_key
        )

        state.is_open = False
        state.last_updated = utcnow()

        return SettlementProof(
            channel_id=channel_id,
            final_nonce=state.nonce,
            final_amount=state.spent_amount,
            sender_sig=sender_sig,
            receiver_sig=receiver_sig,
        )

    def get_channel(self, channel_id: str) -> ChannelState:
        """Retrieve a channel by ID."""
        channel = self._channels.get(channel_id)
        if channel is None:
            raise ChannelError(f"Channel {channel_id} not found")
        return channel

    def get_channels_for(self, address: str) -> list[ChannelState]:
        """List all active channels where address is sender or receiver."""
        return [
            ch
            for ch in self._channels.values()
            if ch.is_open and (ch.sender == address or ch.receiver == address)
        ]

    def _get_open_channel(self, channel_id: str) -> ChannelState:
        """Retrieve a channel and verify it's still open."""
        state = self._channels.get(channel_id)
        if state is None:
            raise ChannelError(f"Channel {channel_id} not found")
        if not state.is_open:
            raise ChannelError(f"Channel {channel_id} is closed")
        return state
