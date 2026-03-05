"""Tests for the revenue settlement engine.

Validates settlement processing, hash generation, earnings tracking,
protocol stats, and channel integration paths.
"""

from __future__ import annotations

import pytest

from blockthrough.channels.manager import ChannelManager
from blockthrough.channels.types import ChannelConfig
from blockthrough.revenue.calculator import calculate_shares
from blockthrough.revenue.settlement import SettlementEngine, SettlementError
from blockthrough.revenue.types import RevenueConfig, SplitBasis, SplitRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(pid: str, weight: float) -> SplitRule:
    return SplitRule(participant_id=pid, basis=SplitBasis.FIXED, weight=weight)


def _calculate_and_settle(
    engine: SettlementEngine,
    execution_id: str = "exec-1",
    cost: float = 100.0,
    rules: list[SplitRule] | None = None,
    fee_pct: float = 3.0,
    burn_pct: float = 30.0,
):
    """Helper: calculate shares then settle in one step."""
    if rules is None:
        rules = [_rule("alice", 60), _rule("bob", 40)]

    shares, protocol_fee = calculate_shares(
        execution_id=execution_id,
        execution_cost=cost,
        split_rules=rules,
        protocol_fee_pct=fee_pct,
        burn_pct=burn_pct,
    )
    return engine.settle(
        execution_id=execution_id,
        shares=shares,
        protocol_fee=protocol_fee,
        total_amount=cost,
    )


# ---------------------------------------------------------------------------
# Basic settlement
# ---------------------------------------------------------------------------


class TestSettlement:

    def test_settle_returns_settlement_record(self) -> None:
        engine = SettlementEngine()
        settlement = _calculate_and_settle(engine)

        assert settlement.id  # non-empty UUID
        assert settlement.execution_id == "exec-1"
        assert settlement.total_amount == 100.0
        assert settlement.settled_at is not None

    def test_settle_contains_all_shares(self) -> None:
        engine = SettlementEngine()
        settlement = _calculate_and_settle(engine)

        assert len(settlement.shares) == 2
        pids = {s.participant_id for s in settlement.shares}
        assert pids == {"alice", "bob"}

    def test_shares_marked_settled_without_channels(self) -> None:
        """Without a ChannelManager, shares are settled via bookkeeping."""
        engine = SettlementEngine()
        settlement = _calculate_and_settle(engine)

        assert all(s.settled for s in settlement.shares)

    def test_settle_protocol_fee_recorded(self) -> None:
        engine = SettlementEngine()
        settlement = _calculate_and_settle(engine)

        assert settlement.protocol_fee.fee_pct == 3.0
        assert settlement.protocol_fee.fee_amount == pytest.approx(3.0)
        assert settlement.protocol_fee.burn_amount == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Settlement hash
# ---------------------------------------------------------------------------


class TestSettlementHash:

    def test_hash_is_non_empty(self) -> None:
        engine = SettlementEngine()
        settlement = _calculate_and_settle(engine)
        assert len(settlement.settlement_hash) == 64  # SHA-256 hex

    def test_hash_is_deterministic(self) -> None:
        """Same inputs produce the same hash."""
        engine1 = SettlementEngine()
        engine2 = SettlementEngine()

        s1 = _calculate_and_settle(engine1, execution_id="det-1")
        s2 = _calculate_and_settle(engine2, execution_id="det-1")

        assert s1.settlement_hash == s2.settlement_hash

    def test_different_inputs_produce_different_hash(self) -> None:
        engine = SettlementEngine()

        s1 = _calculate_and_settle(engine, execution_id="a", cost=100.0)
        s2 = _calculate_and_settle(engine, execution_id="b", cost=200.0)

        assert s1.settlement_hash != s2.settlement_hash


# ---------------------------------------------------------------------------
# Earnings tracking
# ---------------------------------------------------------------------------


class TestEarnings:

    def test_earnings_tracked_per_participant(self) -> None:
        engine = SettlementEngine()
        _calculate_and_settle(engine)

        # 3% fee on 100 = 3. Distributable = 97. alice: 60%, bob: 40%
        assert engine.get_earnings("alice") == pytest.approx(58.2)
        assert engine.get_earnings("bob") == pytest.approx(38.8)

    def test_earnings_accumulate_across_settlements(self) -> None:
        engine = SettlementEngine()
        _calculate_and_settle(engine, execution_id="e1")
        _calculate_and_settle(engine, execution_id="e2")

        # Each settlement: alice gets 58.2, bob gets 38.8
        assert engine.get_earnings("alice") == pytest.approx(116.4)
        assert engine.get_earnings("bob") == pytest.approx(77.6)

    def test_earnings_zero_for_unknown_participant(self) -> None:
        engine = SettlementEngine()
        assert engine.get_earnings("unknown") == 0.0

    def test_get_all_earnings(self) -> None:
        engine = SettlementEngine()
        _calculate_and_settle(engine)

        all_earnings = engine.get_all_earnings()
        assert "alice" in all_earnings
        assert "bob" in all_earnings
        assert len(all_earnings) == 2


# ---------------------------------------------------------------------------
# Settlement retrieval
# ---------------------------------------------------------------------------


class TestGetSettlement:

    def test_get_settlement_by_id(self) -> None:
        engine = SettlementEngine()
        settlement = _calculate_and_settle(engine)

        fetched = engine.get_settlement(settlement.id)
        assert fetched is not None
        assert fetched.id == settlement.id
        assert fetched.execution_id == settlement.execution_id

    def test_get_settlement_returns_none_for_unknown(self) -> None:
        engine = SettlementEngine()
        assert engine.get_settlement("nonexistent") is None


# ---------------------------------------------------------------------------
# Protocol stats
# ---------------------------------------------------------------------------


class TestProtocolStats:

    def test_stats_after_one_settlement(self) -> None:
        engine = SettlementEngine()
        _calculate_and_settle(engine)

        stats = engine.get_protocol_stats()
        assert stats["total_settlements"] == 1
        assert stats["total_fees_collected"] == pytest.approx(3.0)
        assert stats["total_burned"] == pytest.approx(0.9)
        assert stats["total_volume"] == pytest.approx(100.0)

    def test_stats_accumulate(self) -> None:
        engine = SettlementEngine()
        _calculate_and_settle(engine, execution_id="e1", cost=100.0)
        _calculate_and_settle(engine, execution_id="e2", cost=200.0)

        stats = engine.get_protocol_stats()
        assert stats["total_settlements"] == 2
        # Fee on 100 = 3.0, fee on 200 = 6.0 => total = 9.0
        assert stats["total_fees_collected"] == pytest.approx(9.0)
        assert stats["total_burned"] == pytest.approx(2.7)
        assert stats["total_volume"] == pytest.approx(300.0)

    def test_stats_empty_engine(self) -> None:
        engine = SettlementEngine()
        stats = engine.get_protocol_stats()

        assert stats["total_settlements"] == 0
        assert stats["total_fees_collected"] == 0.0


# ---------------------------------------------------------------------------
# Minimum settlement threshold
# ---------------------------------------------------------------------------


class TestMinSettlement:

    def test_below_minimum_raises(self) -> None:
        config = RevenueConfig(min_settlement=1.0)
        engine = SettlementEngine(config=config)

        with pytest.raises(SettlementError, match="below minimum"):
            _calculate_and_settle(engine, cost=0.5)

    def test_at_minimum_succeeds(self) -> None:
        config = RevenueConfig(min_settlement=1.0)
        engine = SettlementEngine(config=config)

        settlement = _calculate_and_settle(engine, cost=1.0)
        assert settlement.total_amount == 1.0


# ---------------------------------------------------------------------------
# Channel integration
# ---------------------------------------------------------------------------


class TestChannelIntegration:

    def test_settlement_with_channel_payment(self) -> None:
        """When a channel exists, share payments go through it."""
        ch_mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        engine = SettlementEngine(channel_manager=ch_mgr)

        # Open a channel from some sender to "alice" (the participant)
        ch_mgr.open_channel("protocol", "alice", deposit=200.0)

        settlement = _calculate_and_settle(engine, cost=100.0)

        # Alice's share should be settled via channel
        alice_share = next(s for s in settlement.shares if s.participant_id == "alice")
        assert alice_share.settled is True

        # Bob has no channel, so settled is False
        bob_share = next(s for s in settlement.shares if s.participant_id == "bob")
        assert bob_share.settled is False

    def test_settlement_without_sufficient_deposit(self) -> None:
        """Channel exists but deposit is too small for the share."""
        ch_mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        engine = SettlementEngine(channel_manager=ch_mgr)

        # Tiny deposit — won't cover alice's ~58.2 share
        ch_mgr.open_channel("protocol", "alice", deposit=1.0)

        settlement = _calculate_and_settle(engine, cost=100.0)

        alice_share = next(s for s in settlement.shares if s.participant_id == "alice")
        assert alice_share.settled is False

    def test_earnings_tracked_regardless_of_channel(self) -> None:
        """Earnings accumulate even when channel payment fails."""
        ch_mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        engine = SettlementEngine(channel_manager=ch_mgr)

        _calculate_and_settle(engine, cost=100.0)

        # Both participants have earnings tracked
        assert engine.get_earnings("alice") == pytest.approx(58.2)
        assert engine.get_earnings("bob") == pytest.approx(38.8)


# ---------------------------------------------------------------------------
# Single participant edge case
# ---------------------------------------------------------------------------


class TestSingleParticipant:

    def test_single_participant_gets_full_distributable(self) -> None:
        engine = SettlementEngine()
        rules = [_rule("solo", 100)]
        settlement = _calculate_and_settle(
            engine, rules=rules, cost=100.0, fee_pct=3.0
        )

        assert len(settlement.shares) == 1
        assert settlement.shares[0].amount_usd == pytest.approx(97.0)
        assert engine.get_earnings("solo") == pytest.approx(97.0)
