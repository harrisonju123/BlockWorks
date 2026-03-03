"""Tests for governance type models — validation and serialization."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentproof.governance.types import (
    GovernanceConfig,
    Proposal,
    ProposalStatus,
    Vote,
    VoteSupport,
)


class TestProposalStatus:

    def test_all_statuses_exist(self) -> None:
        expected = {"pending", "active", "passed", "rejected", "executed"}
        actual = {s.value for s in ProposalStatus}
        assert actual == expected


class TestVoteSupport:

    def test_values(self) -> None:
        assert VoteSupport.FOR.value == "for"
        assert VoteSupport.AGAINST.value == "against"


class TestGovernanceConfig:

    def test_defaults(self) -> None:
        config = GovernanceConfig()
        assert config.voting_period_s == 604_800
        assert config.quorum_pct == 10.0
        assert config.proposal_threshold == 0
        assert config.total_supply == 1_000_000_000

    def test_custom_values(self) -> None:
        config = GovernanceConfig(
            voting_period_s=3600,
            quorum_pct=5.0,
            proposal_threshold=500,
            total_supply=100_000,
        )
        assert config.voting_period_s == 3600
        assert config.quorum_pct == 5.0

    def test_quorum_pct_bounds(self) -> None:
        GovernanceConfig(quorum_pct=0.0)
        GovernanceConfig(quorum_pct=100.0)

        with pytest.raises(ValidationError):
            GovernanceConfig(quorum_pct=-1.0)
        with pytest.raises(ValidationError):
            GovernanceConfig(quorum_pct=101.0)

    def test_negative_threshold_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GovernanceConfig(proposal_threshold=-1)


class TestVote:

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Vote(
                proposal_id="p1",
                voter="alice",
                weight=-1,
                support=VoteSupport.FOR,
            )

    def test_zero_weight_allowed(self) -> None:
        v = Vote(
            proposal_id="p1",
            voter="alice",
            weight=0,
            support=VoteSupport.FOR,
        )
        assert v.weight == 0
