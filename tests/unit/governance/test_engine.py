"""Tests for the governance engine — proposal lifecycle and voting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentproof.governance.engine import (
    AlreadyVotedError,
    GovernanceEngine,
    InsufficientTokensError,
    ProposalNotFoundError,
    VotingClosedError,
)
from agentproof.governance.types import (
    GovernanceConfig,
    ProposalStatus,
    VoteSupport,
)


def _engine(**overrides) -> GovernanceEngine:
    config = GovernanceConfig(**overrides)
    return GovernanceEngine(config)


class TestCreateProposal:

    def test_creates_active_proposal(self) -> None:
        engine = _engine()
        p = engine.create_proposal("Title", "Description", "alice")
        assert p.status == ProposalStatus.ACTIVE
        assert p.title == "Title"
        assert p.description == "Description"
        assert p.proposer == "alice"

    def test_assigns_unique_id(self) -> None:
        engine = _engine()
        p1 = engine.create_proposal("A", "desc", "alice")
        p2 = engine.create_proposal("B", "desc", "bob")
        assert p1.id != p2.id

    def test_sets_voting_deadline(self) -> None:
        engine = _engine(voting_period_s=3600)
        before = datetime.now(timezone.utc)
        p = engine.create_proposal("T", "D", "alice")
        after = datetime.now(timezone.utc)

        # Deadline should be ~1 hour from now
        assert p.voting_deadline >= before + timedelta(seconds=3600)
        assert p.voting_deadline <= after + timedelta(seconds=3600)

    def test_initial_vote_counts_are_zero(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        assert p.for_votes == 0
        assert p.against_votes == 0

    def test_proposal_threshold_enforced(self) -> None:
        engine = _engine(proposal_threshold=1000)
        with pytest.raises(InsufficientTokensError):
            engine.create_proposal("T", "D", "alice", proposer_balance=999)

    def test_proposal_threshold_met(self) -> None:
        engine = _engine(proposal_threshold=1000)
        p = engine.create_proposal("T", "D", "alice", proposer_balance=1000)
        assert p.status == ProposalStatus.ACTIVE

    def test_zero_threshold_always_passes(self) -> None:
        engine = _engine(proposal_threshold=0)
        p = engine.create_proposal("T", "D", "alice", proposer_balance=0)
        assert p.status == ProposalStatus.ACTIVE


class TestCastVote:

    def test_vote_for(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        vote = engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=100)

        assert vote.proposal_id == p.id
        assert vote.voter == "bob"
        assert vote.support == VoteSupport.FOR
        assert vote.weight == 100

    def test_vote_against(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.AGAINST, weight=50)

        updated = engine.get_proposal(p.id)
        assert updated.against_votes == 50
        assert updated.for_votes == 0

    def test_multiple_voters_accumulate(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=100)
        engine.cast_vote(p.id, "carol", VoteSupport.FOR, weight=200)
        engine.cast_vote(p.id, "dave", VoteSupport.AGAINST, weight=50)

        updated = engine.get_proposal(p.id)
        assert updated.for_votes == 300
        assert updated.against_votes == 50

    def test_double_vote_rejected(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=100)

        with pytest.raises(AlreadyVotedError):
            engine.cast_vote(p.id, "bob", VoteSupport.AGAINST, weight=50)

    def test_vote_on_nonexistent_proposal(self) -> None:
        engine = _engine()
        with pytest.raises(ProposalNotFoundError):
            engine.cast_vote("bogus-id", "bob", VoteSupport.FOR, weight=100)

    def test_vote_on_closed_proposal(self) -> None:
        engine = _engine(total_supply=100, quorum_pct=10.0)
        p = engine.create_proposal("T", "D", "alice")
        # Cast enough votes to meet quorum and pass
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=20)
        engine.tally(p.id)

        # Proposal is now PASSED — voting should be closed
        with pytest.raises(VotingClosedError):
            engine.cast_vote(p.id, "carol", VoteSupport.AGAINST, weight=10)

    def test_zero_weight_vote_allowed(self) -> None:
        """Zero-weight votes are valid (e.g., symbolic votes)."""
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        vote = engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=0)
        assert vote.weight == 0


class TestTally:

    def test_passes_with_majority_and_quorum(self) -> None:
        engine = _engine(total_supply=1000, quorum_pct=10.0)
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=80)
        engine.cast_vote(p.id, "carol", VoteSupport.AGAINST, weight=30)

        result = engine.tally(p.id)
        assert result.status == ProposalStatus.PASSED

    def test_rejected_with_majority_against(self) -> None:
        engine = _engine(total_supply=1000, quorum_pct=10.0)
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=30)
        engine.cast_vote(p.id, "carol", VoteSupport.AGAINST, weight=80)

        result = engine.tally(p.id)
        assert result.status == ProposalStatus.REJECTED

    def test_stays_active_if_quorum_not_met(self) -> None:
        engine = _engine(total_supply=1000, quorum_pct=10.0)
        p = engine.create_proposal("T", "D", "alice")
        # Only 50 votes out of 100 needed for quorum
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=50)

        result = engine.tally(p.id)
        # Quorum not met and deadline not passed -> stays active
        assert result.status == ProposalStatus.ACTIVE

    def test_rejected_after_deadline_without_quorum(self) -> None:
        engine = _engine(total_supply=1000, quorum_pct=10.0, voting_period_s=1)
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=5)

        # Force the deadline to be in the past
        p.voting_deadline = datetime.now(timezone.utc) - timedelta(seconds=10)

        result = engine.tally(p.id)
        assert result.status == ProposalStatus.REJECTED

    def test_tally_nonexistent_proposal(self) -> None:
        engine = _engine()
        with pytest.raises(ProposalNotFoundError):
            engine.tally("bogus-id")

    def test_tally_idempotent_after_finalization(self) -> None:
        engine = _engine(total_supply=100, quorum_pct=10.0)
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=20)

        r1 = engine.tally(p.id)
        r2 = engine.tally(p.id)
        assert r1.status == r2.status == ProposalStatus.PASSED

    def test_tie_goes_to_rejection(self) -> None:
        """Equal for/against votes = not a majority = rejected."""
        engine = _engine(total_supply=100, quorum_pct=10.0)
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=50)
        engine.cast_vote(p.id, "carol", VoteSupport.AGAINST, weight=50)

        result = engine.tally(p.id)
        assert result.status == ProposalStatus.REJECTED

    def test_zero_quorum_pct_always_quorum(self) -> None:
        """With 0% quorum, any votes meet quorum."""
        engine = _engine(total_supply=1_000_000, quorum_pct=0.0)
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=1)

        result = engine.tally(p.id)
        assert result.status == ProposalStatus.PASSED


class TestGetAndListProposals:

    def test_get_proposal(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        retrieved = engine.get_proposal(p.id)
        assert retrieved.id == p.id

    def test_get_nonexistent_proposal(self) -> None:
        engine = _engine()
        with pytest.raises(ProposalNotFoundError):
            engine.get_proposal("bogus")

    def test_list_proposals_empty(self) -> None:
        engine = _engine()
        assert engine.list_proposals() == []

    def test_list_proposals_ordered_newest_first(self) -> None:
        engine = _engine()
        p1 = engine.create_proposal("First", "D", "alice")
        p2 = engine.create_proposal("Second", "D", "bob")
        p3 = engine.create_proposal("Third", "D", "carol")

        proposals = engine.list_proposals()
        assert len(proposals) == 3
        assert proposals[0].id == p3.id
        assert proposals[2].id == p1.id


class TestGetVotes:

    def test_get_votes(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        engine.cast_vote(p.id, "bob", VoteSupport.FOR, weight=100)
        engine.cast_vote(p.id, "carol", VoteSupport.AGAINST, weight=50)

        votes = engine.get_votes(p.id)
        assert len(votes) == 2

    def test_get_votes_empty(self) -> None:
        engine = _engine()
        p = engine.create_proposal("T", "D", "alice")
        assert engine.get_votes(p.id) == []

    def test_get_votes_nonexistent_proposal(self) -> None:
        engine = _engine()
        with pytest.raises(ProposalNotFoundError):
            engine.get_votes("bogus")


class TestReset:

    def test_reset_clears_all_state(self) -> None:
        engine = _engine()
        engine.create_proposal("T", "D", "alice")
        engine.reset()
        assert engine.list_proposals() == []
