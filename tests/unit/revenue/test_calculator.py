"""Tests for the revenue split calculator.

Validates all four split basis types, protocol fee + burn math,
edge cases (zero cost, single participant), and rounding behavior.
"""

from __future__ import annotations

import pytest

from agentproof.revenue.calculator import SplitCalculationError, calculate_shares
from agentproof.revenue.types import SplitBasis, SplitRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(pid: str, basis: SplitBasis, weight: float) -> SplitRule:
    return SplitRule(participant_id=pid, basis=basis, weight=weight)


# ---------------------------------------------------------------------------
# Token usage basis
# ---------------------------------------------------------------------------


class TestTokenUsageBasis:

    def test_equal_weights_split_evenly(self) -> None:
        rules = [
            _rule("a", SplitBasis.TOKEN_USAGE, 500),
            _rule("b", SplitBasis.TOKEN_USAGE, 500),
        ]
        shares, fee = calculate_shares("exec-1", 100.0, rules, protocol_fee_pct=0.0)

        assert len(shares) == 2
        assert shares[0].amount_usd == pytest.approx(50.0)
        assert shares[1].amount_usd == pytest.approx(50.0)
        assert shares[0].share_pct == pytest.approx(50.0)

    def test_unequal_weights_proportional(self) -> None:
        rules = [
            _rule("a", SplitBasis.TOKEN_USAGE, 300),
            _rule("b", SplitBasis.TOKEN_USAGE, 700),
        ]
        shares, fee = calculate_shares("exec-2", 100.0, rules, protocol_fee_pct=0.0)

        assert shares[0].amount_usd == pytest.approx(30.0)
        assert shares[1].amount_usd == pytest.approx(70.0)

    def test_three_participants(self) -> None:
        rules = [
            _rule("a", SplitBasis.TOKEN_USAGE, 200),
            _rule("b", SplitBasis.TOKEN_USAGE, 300),
            _rule("c", SplitBasis.TOKEN_USAGE, 500),
        ]
        shares, fee = calculate_shares("exec-3", 100.0, rules, protocol_fee_pct=0.0)

        assert shares[0].amount_usd == pytest.approx(20.0)
        assert shares[1].amount_usd == pytest.approx(30.0)
        assert shares[2].amount_usd == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Execution time basis
# ---------------------------------------------------------------------------


class TestExecTimeBasis:

    def test_proportional_to_exec_time(self) -> None:
        # Weights represent milliseconds of execution time
        rules = [
            _rule("fast-agent", SplitBasis.EXEC_TIME, 100),
            _rule("slow-agent", SplitBasis.EXEC_TIME, 900),
        ]
        shares, _ = calculate_shares("exec-4", 10.0, rules, protocol_fee_pct=0.0)

        assert shares[0].amount_usd == pytest.approx(1.0)
        assert shares[1].amount_usd == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# Value-add basis
# ---------------------------------------------------------------------------


class TestValueAddBasis:

    def test_weighted_by_quality_score(self) -> None:
        # Weights represent benchmark quality scores
        rules = [
            _rule("high-quality", SplitBasis.VALUE_ADD, 0.9),
            _rule("low-quality", SplitBasis.VALUE_ADD, 0.1),
        ]
        shares, _ = calculate_shares("exec-5", 100.0, rules, protocol_fee_pct=0.0)

        assert shares[0].amount_usd == pytest.approx(90.0)
        assert shares[1].amount_usd == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Fixed basis
# ---------------------------------------------------------------------------


class TestFixedBasis:

    def test_fixed_percentage_split(self) -> None:
        # Weights are fixed percentages (60/40 split)
        rules = [
            _rule("primary", SplitBasis.FIXED, 60),
            _rule("secondary", SplitBasis.FIXED, 40),
        ]
        shares, _ = calculate_shares("exec-6", 100.0, rules, protocol_fee_pct=0.0)

        assert shares[0].amount_usd == pytest.approx(60.0)
        assert shares[1].amount_usd == pytest.approx(40.0)
        assert shares[0].share_pct == pytest.approx(60.0)
        assert shares[1].share_pct == pytest.approx(40.0)

    def test_single_participant_gets_everything(self) -> None:
        rules = [_rule("solo", SplitBasis.FIXED, 100)]
        shares, _ = calculate_shares("exec-7", 50.0, rules, protocol_fee_pct=0.0)

        assert len(shares) == 1
        assert shares[0].amount_usd == pytest.approx(50.0)
        assert shares[0].share_pct == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Mixed basis types
# ---------------------------------------------------------------------------


class TestMixedBasis:

    def test_mixed_basis_types_share_proportionally(self) -> None:
        """Different basis types all reduce to weight-based proportional split."""
        rules = [
            _rule("tokens", SplitBasis.TOKEN_USAGE, 50),
            _rule("time", SplitBasis.EXEC_TIME, 30),
            _rule("quality", SplitBasis.VALUE_ADD, 20),
        ]
        shares, _ = calculate_shares("exec-mix", 100.0, rules, protocol_fee_pct=0.0)

        assert shares[0].amount_usd == pytest.approx(50.0)
        assert shares[1].amount_usd == pytest.approx(30.0)
        assert shares[2].amount_usd == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Protocol fee and burn
# ---------------------------------------------------------------------------


class TestProtocolFee:

    def test_default_protocol_fee(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        shares, fee = calculate_shares("exec-f1", 100.0, rules)

        assert fee.fee_pct == 3.0
        assert fee.fee_amount == pytest.approx(3.0)
        # Participant gets 100 - 3 = 97
        assert shares[0].amount_usd == pytest.approx(97.0)

    def test_custom_protocol_fee(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        shares, fee = calculate_shares(
            "exec-f2", 100.0, rules, protocol_fee_pct=5.0
        )

        assert fee.fee_pct == 5.0
        assert fee.fee_amount == pytest.approx(5.0)
        assert shares[0].amount_usd == pytest.approx(95.0)

    def test_zero_protocol_fee(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        shares, fee = calculate_shares(
            "exec-f3", 100.0, rules, protocol_fee_pct=0.0
        )

        assert fee.fee_amount == 0.0
        assert shares[0].amount_usd == pytest.approx(100.0)

    def test_burn_amount_calculation(self) -> None:
        """30% of the 3% protocol fee is burned."""
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        _, fee = calculate_shares(
            "exec-b1", 100.0, rules, protocol_fee_pct=3.0, burn_pct=30.0
        )

        assert fee.fee_amount == pytest.approx(3.0)
        assert fee.burn_amount == pytest.approx(0.9)

    def test_full_burn(self) -> None:
        """100% burn means entire protocol fee is burned."""
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        _, fee = calculate_shares(
            "exec-b2", 100.0, rules, protocol_fee_pct=5.0, burn_pct=100.0
        )

        assert fee.burn_amount == pytest.approx(5.0)

    def test_no_burn(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        _, fee = calculate_shares(
            "exec-b3", 100.0, rules, protocol_fee_pct=5.0, burn_pct=0.0
        )

        assert fee.burn_amount == 0.0

    def test_fee_deducted_before_split(self) -> None:
        """Two equal participants share what's left after the protocol fee."""
        rules = [
            _rule("a", SplitBasis.FIXED, 50),
            _rule("b", SplitBasis.FIXED, 50),
        ]
        shares, fee = calculate_shares(
            "exec-f4", 100.0, rules, protocol_fee_pct=10.0
        )

        # 10% fee => 90 distributable, split 50/50
        assert fee.fee_amount == pytest.approx(10.0)
        assert shares[0].amount_usd == pytest.approx(45.0)
        assert shares[1].amount_usd == pytest.approx(45.0)

    def test_execution_id_propagates(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        shares, fee = calculate_shares("my-exec-id", 10.0, rules)

        assert fee.execution_id == "my-exec-id"
        assert shares[0].workflow_execution_id == "my-exec-id"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_zero_cost_gives_zero_amounts(self) -> None:
        rules = [
            _rule("a", SplitBasis.FIXED, 60),
            _rule("b", SplitBasis.FIXED, 40),
        ]
        shares, fee = calculate_shares("exec-z", 0.0, rules)

        assert fee.fee_amount == 0.0
        assert fee.burn_amount == 0.0
        assert shares[0].amount_usd == 0.0
        assert shares[1].amount_usd == 0.0
        # Percentages still reflect the weights
        assert shares[0].share_pct == pytest.approx(60.0)

    def test_very_small_amount(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        shares, fee = calculate_shares("exec-tiny", 0.0001, rules, protocol_fee_pct=3.0)

        assert fee.fee_amount > 0
        assert shares[0].amount_usd > 0
        assert shares[0].amount_usd < 0.0001

    def test_shares_not_settled_by_default(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        shares, _ = calculate_shares("exec-s", 10.0, rules)

        assert shares[0].settled is False

    def test_negative_cost_raises(self) -> None:
        rules = [_rule("a", SplitBasis.FIXED, 100)]
        with pytest.raises(SplitCalculationError, match="non-negative"):
            calculate_shares("exec-neg", -10.0, rules)

    def test_empty_rules_raises(self) -> None:
        with pytest.raises(SplitCalculationError, match="must not be empty"):
            calculate_shares("exec-empty", 10.0, [])

    def test_all_zero_weights_raises(self) -> None:
        rules = [
            _rule("a", SplitBasis.FIXED, 0),
            _rule("b", SplitBasis.FIXED, 0),
        ]
        with pytest.raises(SplitCalculationError, match="must be positive"):
            calculate_shares("exec-zerw", 10.0, rules)

    def test_rounding_does_not_create_dust(self) -> None:
        """Verify amounts are rounded to 8 decimal places to avoid float dust."""
        rules = [
            _rule("a", SplitBasis.FIXED, 1),
            _rule("b", SplitBasis.FIXED, 1),
            _rule("c", SplitBasis.FIXED, 1),
        ]
        shares, fee = calculate_shares("exec-round", 1.0, rules, protocol_fee_pct=3.0)

        for share in shares:
            # Amount should be cleanly rounded
            assert share.amount_usd == round(share.amount_usd, 8)
