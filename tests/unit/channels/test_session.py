"""Tests for the SessionManager agent session lifecycle integration.

Validates that the session manager correctly maps agent sessions to
state channels: start -> record usage -> end, with budget enforcement.
"""

from __future__ import annotations

import pytest

from blockthrough.channels.manager import ChannelError
from blockthrough.channels.session import SessionManager
from blockthrough.channels.types import ChannelConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER = "user-001"
PROVIDER = "provider-001"
BUDGET = 5.0
SENDER_KEY = "user-key"
RECEIVER_KEY = "provider-key"


def _make_session_mgr(min_deposit: float = 0.01) -> SessionManager:
    return SessionManager(config=ChannelConfig(min_deposit=min_deposit))


# ---------------------------------------------------------------------------
# Start session
# ---------------------------------------------------------------------------


class TestStartSession:

    def test_start_returns_session_id(self) -> None:
        mgr = _make_session_mgr()
        session_id = mgr.start_session(
            USER, PROVIDER, BUDGET, sender_key=SENDER_KEY, receiver_key=RECEIVER_KEY
        )
        assert isinstance(session_id, str)
        assert len(session_id) > 0

    def test_start_multiple_sessions(self) -> None:
        mgr = _make_session_mgr()
        s1 = mgr.start_session(USER, PROVIDER, 1.0)
        s2 = mgr.start_session(USER, "other-provider", 2.0)
        assert s1 != s2

    def test_start_rejects_budget_below_minimum(self) -> None:
        mgr = _make_session_mgr(min_deposit=1.0)
        with pytest.raises(ChannelError, match="below minimum"):
            mgr.start_session(USER, PROVIDER, 0.5)


# ---------------------------------------------------------------------------
# Record usage
# ---------------------------------------------------------------------------


class TestRecordUsage:

    def test_record_usage_tracks_spend(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, BUDGET)

        mgr.record_usage(sid, 1.0)
        assert mgr.get_session_spend(sid) == 1.0

        mgr.record_usage(sid, 0.5)
        assert mgr.get_session_spend(sid) == 1.5

    def test_record_usage_rejects_budget_overflow(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, 2.0)

        mgr.record_usage(sid, 1.5)
        with pytest.raises(ChannelError, match="exceed deposit"):
            mgr.record_usage(sid, 1.0)  # 1.5 + 1.0 = 2.5 > 2.0

    def test_record_usage_allows_exact_budget(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, 1.0)

        mgr.record_usage(sid, 1.0)  # Exactly at limit
        assert mgr.get_session_spend(sid) == 1.0

    def test_record_usage_rejects_unknown_session(self) -> None:
        mgr = _make_session_mgr()
        with pytest.raises(ChannelError, match="Session .* not found"):
            mgr.record_usage("nonexistent", 0.1)

    def test_record_usage_rejects_ended_session(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, BUDGET)
        mgr.end_session(sid)

        with pytest.raises(ChannelError, match="not found"):
            mgr.record_usage(sid, 0.1)


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------


class TestEndSession:

    def test_end_returns_settlement_proof(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(
            USER, PROVIDER, BUDGET, sender_key=SENDER_KEY, receiver_key=RECEIVER_KEY
        )

        mgr.record_usage(sid, 2.0)
        mgr.record_usage(sid, 0.5)

        proof = mgr.end_session(sid)
        assert proof.final_amount == 2.5
        assert proof.final_nonce == 2
        assert len(proof.sender_sig) == 64
        assert len(proof.receiver_sig) == 64

    def test_end_without_usage(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, BUDGET)
        proof = mgr.end_session(sid)

        assert proof.final_amount == 0.0
        assert proof.final_nonce == 0

    def test_end_removes_session(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, BUDGET)
        mgr.end_session(sid)

        with pytest.raises(ChannelError, match="not found"):
            mgr.end_session(sid)

    def test_end_rejects_unknown_session(self) -> None:
        mgr = _make_session_mgr()
        with pytest.raises(ChannelError, match="not found"):
            mgr.end_session("nonexistent")


# ---------------------------------------------------------------------------
# Budget and spend queries
# ---------------------------------------------------------------------------


class TestQueries:

    def test_get_session_budget(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, 7.5)
        assert mgr.get_session_budget(sid) == 7.5

    def test_get_session_spend_starts_at_zero(self) -> None:
        mgr = _make_session_mgr()
        sid = mgr.start_session(USER, PROVIDER, BUDGET)
        assert mgr.get_session_spend(sid) == 0.0


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestFullSessionLifecycle:

    def test_full_lifecycle(self) -> None:
        """Start session, record multiple usages, check budget, end session."""
        mgr = _make_session_mgr()
        sid = mgr.start_session(
            USER, PROVIDER, 10.0, sender_key=SENDER_KEY, receiver_key=RECEIVER_KEY
        )

        assert mgr.get_session_budget(sid) == 10.0
        assert mgr.get_session_spend(sid) == 0.0

        mgr.record_usage(sid, 3.0)
        mgr.record_usage(sid, 2.5)
        mgr.record_usage(sid, 1.0)

        assert mgr.get_session_spend(sid) == 6.5

        proof = mgr.end_session(sid)
        assert proof.final_amount == 6.5
        assert proof.final_nonce == 3

    def test_multiple_sessions_independent(self) -> None:
        """Multiple sessions don't interfere with each other's state."""
        mgr = _make_session_mgr()
        s1 = mgr.start_session(USER, "prov-a", 5.0)
        s2 = mgr.start_session(USER, "prov-b", 3.0)

        mgr.record_usage(s1, 2.0)
        mgr.record_usage(s2, 1.0)

        assert mgr.get_session_spend(s1) == 2.0
        assert mgr.get_session_spend(s2) == 1.0

        proof1 = mgr.end_session(s1)
        assert proof1.final_amount == 2.0

        # s2 still active after s1 closed
        mgr.record_usage(s2, 0.5)
        proof2 = mgr.end_session(s2)
        assert proof2.final_amount == 1.5
