"""Governance API endpoints — proposal lifecycle and voting."""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException

from blockthrough.governance.engine import (
    AlreadyVotedError,
    GovernanceEngine,
    InsufficientTokensError,
    ProposalNotFoundError,
    VotingClosedError,
)
from blockthrough.governance.types import (
    GovernanceConfig,
    Proposal,
    ProposalStatus,
    Vote,
    VoteSupport,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Singleton engine — replaced by DI in production
# ---------------------------------------------------------------------------

_engine = GovernanceEngine(GovernanceConfig())


def get_engine() -> GovernanceEngine:
    return _engine


def reset_engine() -> None:
    """Reset the singleton engine. Used by tests."""
    _engine.reset()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ProposalCreate(BaseModel):
    title: str
    description: str
    proposer: str
    proposer_balance: int = Field(default=0, ge=0)


class ProposalResponse(BaseModel):
    id: str
    title: str
    description: str
    proposer: str
    created_at: str
    voting_deadline: str
    for_votes: int
    against_votes: int
    status: ProposalStatus


class VoteCast(BaseModel):
    voter: str
    support: VoteSupport
    weight: int = Field(ge=0)


class VoteResponse(BaseModel):
    proposal_id: str
    voter: str
    weight: int
    support: VoteSupport


class ProposalDetailResponse(BaseModel):
    proposal: ProposalResponse
    votes: list[VoteResponse]
    quorum_pct: float
    total_votes: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proposal_to_response(p: Proposal) -> ProposalResponse:
    return ProposalResponse(
        id=p.id,
        title=p.title,
        description=p.description,
        proposer=p.proposer,
        created_at=p.created_at.isoformat(),
        voting_deadline=p.voting_deadline.isoformat(),
        for_votes=p.for_votes,
        against_votes=p.against_votes,
        status=p.status,
    )


def _vote_to_response(v: Vote) -> VoteResponse:
    return VoteResponse(
        proposal_id=v.proposal_id,
        voter=v.voter,
        weight=v.weight,
        support=v.support,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/governance/proposals",
    response_model=ProposalResponse,
    status_code=201,
)
async def create_proposal(body: ProposalCreate) -> ProposalResponse:
    engine = get_engine()
    try:
        proposal = engine.create_proposal(
            title=body.title,
            description=body.description,
            proposer=body.proposer,
            proposer_balance=body.proposer_balance,
        )
    except InsufficientTokensError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return _proposal_to_response(proposal)


@router.get(
    "/governance/proposals",
    response_model=list[ProposalResponse],
)
async def list_proposals() -> list[ProposalResponse]:
    engine = get_engine()
    return [_proposal_to_response(p) for p in engine.list_proposals()]


@router.post(
    "/governance/proposals/{proposal_id}/vote",
    response_model=VoteResponse,
)
async def cast_vote(proposal_id: str, body: VoteCast) -> VoteResponse:
    engine = get_engine()
    try:
        vote = engine.cast_vote(
            proposal_id=proposal_id,
            voter=body.voter,
            support=body.support,
            weight=body.weight,
        )
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except VotingClosedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except AlreadyVotedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _vote_to_response(vote)


@router.get(
    "/governance/proposals/{proposal_id}",
    response_model=ProposalDetailResponse,
)
async def get_proposal_detail(proposal_id: str) -> ProposalDetailResponse:
    engine = get_engine()
    try:
        # Tally to get current status
        proposal = engine.tally(proposal_id)
        votes = engine.get_votes(proposal_id)
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found")

    return ProposalDetailResponse(
        proposal=_proposal_to_response(proposal),
        votes=[_vote_to_response(v) for v in votes],
        quorum_pct=engine.config.quorum_pct,
        total_votes=proposal.for_votes + proposal.against_votes,
    )
