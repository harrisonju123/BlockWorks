"""Agent session lifecycle integration with state channels.

Maps the natural agent session flow (start -> use tools -> end) onto
state channel operations (open -> pay -> close). Each session gets
its own channel with the budget as the deposit cap.
"""

from __future__ import annotations

from agentproof.channels.manager import ChannelError, ChannelManager
from agentproof.channels.types import ChannelConfig, SettlementProof


class SessionManager:
    """Manages agent sessions backed by state channels.

    Wraps ChannelManager to provide a session-oriented API that
    maps to agent lifecycle events.
    """

    def __init__(self, config: ChannelConfig | None = None) -> None:
        self._channel_mgr = ChannelManager(config=config)
        # session_id -> channel_id mapping
        self._sessions: dict[str, str] = {}
        # session_id -> sender_key for signing
        self._session_keys: dict[str, str] = {}
        # session_id -> receiver_key for close co-signing
        self._receiver_keys: dict[str, str] = {}

    def start_session(
        self,
        user_id: str,
        provider_id: str,
        budget: float,
        sender_key: str = "default-sender-key",
        receiver_key: str = "default-receiver-key",
    ) -> str:
        """Open a channel with the budget as deposit, return session_id.

        The session_id is the channel_id — a 1:1 mapping that simplifies
        the lookup path for usage recording and close.
        """
        state = self._channel_mgr.open_channel(
            sender=user_id,
            receiver=provider_id,
            deposit=budget,
            sender_key=sender_key,
        )
        session_id = state.channel_id
        self._sessions[session_id] = state.channel_id
        self._session_keys[session_id] = sender_key
        self._receiver_keys[session_id] = receiver_key
        return session_id

    def record_usage(self, session_id: str, amount: float) -> None:
        """Record tool/MCP usage cost as a payment on the session channel.

        Raises ChannelError if the cumulative spend would exceed the budget
        (deposit), or if the session is already closed.
        """
        channel_id = self._resolve_session(session_id)
        self._channel_mgr.create_payment(channel_id, amount)

    def end_session(self, session_id: str) -> SettlementProof:
        """Close the session channel and return a settlement proof."""
        channel_id = self._resolve_session(session_id)
        receiver_key = self._receiver_keys.get(session_id, "default-receiver-key")
        proof = self._channel_mgr.close_channel(channel_id, receiver_key=receiver_key)

        # Clean up session mappings
        del self._sessions[session_id]
        self._session_keys.pop(session_id, None)
        self._receiver_keys.pop(session_id, None)

        return proof

    def get_session_spend(self, session_id: str) -> float:
        """Return cumulative spend for a session."""
        channel_id = self._resolve_session(session_id)
        state = self._channel_mgr.get_channel(channel_id)
        return state.spent_amount

    def get_session_budget(self, session_id: str) -> float:
        """Return the budget (deposit) for a session."""
        channel_id = self._resolve_session(session_id)
        state = self._channel_mgr.get_channel(channel_id)
        return state.deposit_amount

    def _resolve_session(self, session_id: str) -> str:
        """Map session_id to channel_id, raising on unknown session."""
        channel_id = self._sessions.get(session_id)
        if channel_id is None:
            raise ChannelError(f"Session {session_id} not found")
        return channel_id
