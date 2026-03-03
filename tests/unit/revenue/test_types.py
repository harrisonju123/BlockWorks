"""Tests for revenue sharing type models.

Validates Pydantic model construction, defaults, and constraints.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentproof.revenue.types import (
    ProtocolFee,
    RevenueConfig,
    RevenueShare,
    Settlement,
    SplitBasis,
    SplitRule,
)


class TestSplitBasis:

    def test_all_basis_values(self) -> None:
        assert SplitBasis.TOKEN_USAGE == "token_usage"
        assert SplitBasis.EXEC_TIME == "exec_time"
        assert SplitBasis.VALUE_ADD == "value_add"
        assert SplitBasis.FIXED == "fixed"

    def test_enum_count(self) -> None:
        assert len(SplitBasis) == 4


class TestSplitRule:

    def test_construction(self) -> None:
        rule = SplitRule(
            participant_id="alice",
            basis=SplitBasis.TOKEN_USAGE,
            weight=0.5,
        )
        assert rule.participant_id == "alice"
        assert rule.basis == SplitBasis.TOKEN_USAGE
        assert rule.weight == 0.5

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SplitRule(
                participant_id="alice",
                basis=SplitBasis.FIXED,
                weight=-1.0,
            )


class TestRevenueShare:

    def test_defaults(self) -> None:
        share = RevenueShare(
            workflow_execution_id="exec-1",
            participant_id="alice",
            share_pct=50.0,
            amount_usd=25.0,
        )
        assert share.settled is False

    def test_share_pct_bounds(self) -> None:
        # Over 100 should fail
        with pytest.raises(ValidationError):
            RevenueShare(
                workflow_execution_id="exec-1",
                participant_id="alice",
                share_pct=101.0,
                amount_usd=0.0,
            )

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RevenueShare(
                workflow_execution_id="exec-1",
                participant_id="alice",
                share_pct=50.0,
                amount_usd=-1.0,
            )


class TestProtocolFee:

    def test_construction(self) -> None:
        fee = ProtocolFee(
            execution_id="exec-1",
            fee_pct=3.0,
            fee_amount=3.0,
            burn_amount=0.9,
        )
        assert fee.fee_pct == 3.0
        assert fee.burn_amount == 0.9


class TestSettlement:

    def test_defaults(self) -> None:
        fee = ProtocolFee(
            execution_id="exec-1",
            fee_pct=3.0,
            fee_amount=3.0,
            burn_amount=0.9,
        )
        settlement = Settlement(
            id="s-1",
            execution_id="exec-1",
            shares=[],
            protocol_fee=fee,
            total_amount=100.0,
        )
        assert settlement.settled_at is None
        assert settlement.settlement_hash == ""


class TestRevenueConfig:

    def test_defaults(self) -> None:
        config = RevenueConfig()
        assert config.protocol_fee_pct == 3.0
        assert config.burn_pct == 30.0
        assert config.min_settlement == 0.001

    def test_custom_values(self) -> None:
        config = RevenueConfig(
            protocol_fee_pct=5.0,
            burn_pct=50.0,
            min_settlement=0.01,
        )
        assert config.protocol_fee_pct == 5.0
        assert config.burn_pct == 50.0

    def test_fee_pct_over_100_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RevenueConfig(protocol_fee_pct=101.0)

    def test_negative_min_settlement_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RevenueConfig(min_settlement=-0.01)
