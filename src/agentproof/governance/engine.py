"""In-memory governance engine for proposal lifecycle management.

Handles creation, voting, tallying, and status transitions. Token-weighted:
vote weight equals the voter's balance at cast time. Quorum is checked
against total_supply from GovernanceConfig.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from agentproof.governance.types import (
    GovernanceConfig,
    Proposal,
    ProposalStatus,
    Vote,
    VoteSupport,
)
from agentproof.utils import utcnow


class GovernanceError(Exception):
    """Base error for governance operations."""


class ProposalNotFoundError(GovernanceError):
    pass


class VotingClosedError(GovernanceError):
    pass


class AlreadyVotedError(GovernanceError):
    pass


class InsufficientTokensError(GovernanceError):
    pass


class GovernanceEngine:
    """In-memory governance engine.

    Stores proposals and votes in dicts. Production would persist to DB,
    but the interface stays the same.
    """

    def __init__(self, config: GovernanceConfig | None = None) -> None:
        self._config = config or GovernanceConfig()
        self._proposals: dict[str, Proposal] = {}
        self._votes: dict[str, list[Vote]] = {}  # proposal_id -> votes
        # Track per-proposal voters to prevent double-voting
        self._voter_set: dict[str, set[str]] = {}  # proposal_id -> set of voter ids

    @property
    def config(self) -> GovernanceConfig:
        return self._config

    def create_proposal(
        self,
        title: str,
        description: str,
        proposer: str,
        proposer_balance: int = 0,
    ) -> Proposal:
        """Create a new governance proposal.

        Args:
            title: Short proposal title.
            description: Full proposal description.
            proposer: Identifier of the proposer.
            proposer_balance: Proposer's token balance (checked against threshold).

        Returns:
            The created Proposal in ACTIVE status.

        Raises:
            InsufficientTokensError: If proposer_balance < proposal_threshold.
        """
        if proposer_balance < self._config.proposal_threshold:
            raise InsufficientTokensError(
                f"Need {self._config.proposal_threshold} tokens to propose, "
                f"have {proposer_balance}"
            )

        now = utcnow()
        proposal_id = str(uuid.uuid4())

        proposal = Proposal(
            id=proposal_id,
            title=title,
            description=description,
            proposer=proposer,
            created_at=now,
            voting_deadline=now + timedelta(seconds=self._config.voting_period_s),
            status=ProposalStatus.ACTIVE,
        )

        self._proposals[proposal_id] = proposal
        self._votes[proposal_id] = []
        self._voter_set[proposal_id] = set()

        return proposal

    def cast_vote(
        self,
        proposal_id: str,
        voter: str,
        support: VoteSupport,
        weight: int,
    ) -> Vote:
        """Cast a vote on a proposal.

        Args:
            proposal_id: The proposal to vote on.
            voter: Identifier of the voter.
            support: FOR or AGAINST.
            weight: Token-weighted vote power.

        Returns:
            The recorded Vote.

        Raises:
            ProposalNotFoundError: If proposal doesn't exist.
            VotingClosedError: If proposal is not in ACTIVE status.
            AlreadyVotedError: If this voter already voted on this proposal.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        if proposal.status != ProposalStatus.ACTIVE:
            raise VotingClosedError(
                f"Proposal {proposal_id} is {proposal.status.value}, not active"
            )

        if utcnow() >= proposal.voting_deadline:
            raise VotingClosedError(
                f"Proposal {proposal_id} voting deadline has passed"
            )

        if voter in self._voter_set[proposal_id]:
            raise AlreadyVotedError(
                f"Voter {voter} already voted on proposal {proposal_id}"
            )

        vote = Vote(
            proposal_id=proposal_id,
            voter=voter,
            weight=weight,
            support=support,
        )

        self._votes[proposal_id].append(vote)
        self._voter_set[proposal_id].add(voter)

        # Update running tallies
        if support == VoteSupport.FOR:
            proposal.for_votes += weight
        else:
            proposal.against_votes += weight

        return vote

    def tally(self, proposal_id: str) -> Proposal:
        """Tally votes and update proposal status.

        Checks quorum (total votes >= quorum_pct of total_supply) and
        simple majority. If quorum is not met, status remains ACTIVE
        (unless deadline passed, then REJECTED).

        Returns:
            The updated Proposal with final status.

        Raises:
            ProposalNotFoundError: If proposal doesn't exist.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        # Already finalized
        if proposal.status in (
            ProposalStatus.PASSED,
            ProposalStatus.REJECTED,
            ProposalStatus.EXECUTED,
        ):
            return proposal

        total_votes = proposal.for_votes + proposal.against_votes
        quorum_threshold = int(
            self._config.total_supply * self._config.quorum_pct / 100
        )
        quorum_met = total_votes >= quorum_threshold

        now = utcnow()
        deadline_passed = now >= proposal.voting_deadline

        if quorum_met:
            # Simple majority decides
            if proposal.for_votes > proposal.against_votes:
                proposal.status = ProposalStatus.PASSED
            else:
                proposal.status = ProposalStatus.REJECTED
        elif deadline_passed:
            # Quorum not met after deadline -> rejected
            proposal.status = ProposalStatus.REJECTED

        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        """Get a proposal by ID.

        Raises:
            ProposalNotFoundError: If proposal doesn't exist.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")
        return proposal

    def list_proposals(self) -> list[Proposal]:
        """List all proposals, newest first."""
        return sorted(
            self._proposals.values(),
            key=lambda p: p.created_at,
            reverse=True,
        )

    def get_votes(self, proposal_id: str) -> list[Vote]:
        """Get all votes for a proposal.

        Raises:
            ProposalNotFoundError: If proposal doesn't exist.
        """
        if proposal_id not in self._proposals:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")
        return list(self._votes.get(proposal_id, []))

    def reset(self) -> None:
        """Clear all state. Used by tests."""
        self._proposals.clear()
        self._votes.clear()
        self._voter_set.clear()
