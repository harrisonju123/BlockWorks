"""Consensus-wrapped attestation provider.

Interposes a multi-validator proposal/vote/finalize flow between callers
and an inner provider (LocalProvider or EVMProvider). Attestations are
not written through until the proposal reaches stake-weighted supermajority.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from blockthrough.attestation.provider import AttestationError, AttestationProvider
from blockthrough.attestation.types import AttestationRecord
from blockthrough.utils import utcnow
from blockthrough.validators.consensus import SUPERMAJORITY_RATIO, StakeWeightedConsensusEngine
from blockthrough.validators.economics import ValidatorEconomics
from blockthrough.validators.registry import ValidatorRegistry
from blockthrough.validators.types import (
    AttestationChallenge,
    ChallengeStatus,
    ConsensusProposal,
    ProposalStatus,
)

# Match Solidity CHALLENGE_PERIOD (600 seconds = 10 min)
_CHALLENGE_PERIOD = timedelta(seconds=600)

logger = logging.getLogger(__name__)


class ConsensusProvider(AttestationProvider):
    """Wraps an inner provider with multi-validator consensus.

    Proposals collect votes weighted by validator stake. Only when
    supermajority is reached does the inner provider receive the
    attestation. Challenges and dispute resolution are handled
    off-chain through the economics module.
    """

    def __init__(
        self,
        inner: AttestationProvider,
        registry: ValidatorRegistry,
        consensus: StakeWeightedConsensusEngine,
        economics: ValidatorEconomics,
    ) -> None:
        self._inner = inner
        self._registry = registry
        self._consensus = consensus
        self._economics = economics

        # In-memory proposal/challenge stores
        self._proposals: dict[str, ConsensusProposal] = {}
        # (org_id_hash, nonce) -> proposal_id to prevent competing proposals
        self._proposal_slots: dict[tuple[str, int], str] = {}
        self._challenges: dict[str, AttestationChallenge] = {}
        self._pending_records: dict[str, AttestationRecord] = {}

    # ── Proposal flow ─────────────────────────────────────────────────

    def create_proposal(
        self,
        record: AttestationRecord,
        proposer: str,
    ) -> ConsensusProposal:
        """Create a new proposal for an attestation record.

        The proposer must be an active validator. Auto-votes yes.
        """
        info = self._registry.get_validator(proposer)
        if info is None or not info.is_active:
            raise AttestationError(f"Proposer {proposer} is not an active validator")

        slot_key = (record.org_id_hash, record.nonce)
        if slot_key in self._proposal_slots:
            raise AttestationError(
                f"Proposal already exists for org={record.org_id_hash} nonce={record.nonce}"
            )

        proposal_id = str(uuid.uuid4())
        now = utcnow()

        proposal = ConsensusProposal(
            proposal_id=proposal_id,
            org_id_hash=record.org_id_hash,
            period_start=record.period_start,
            period_end=record.period_end,
            metrics_hash=record.metrics_hash,
            benchmark_hash=record.benchmark_hash,
            merkle_root=record.merkle_root,
            prev_hash=record.prev_hash,
            attest_nonce=record.nonce,
            proposer=proposer,
            created_at=now,
            total_participating_stake=info.stake_amount,
            yes_stake=info.stake_amount,
            voters=[proposer],
            yes_voters=[proposer],
        )

        self._proposals[proposal_id] = proposal
        self._proposal_slots[slot_key] = proposal_id
        self._pending_records[proposal_id] = record

        logger.info(
            "Proposal %s created by %s for org=%s nonce=%d",
            proposal_id, proposer, record.org_id_hash, record.nonce,
        )
        return proposal

    def vote(
        self,
        proposal_id: str,
        voter: str,
        in_favor: bool,
    ) -> ConsensusProposal:
        """Cast a stake-weighted vote on a proposal."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise AttestationError(f"Proposal {proposal_id} not found")
        if proposal.status != ProposalStatus.PENDING:
            raise AttestationError(f"Proposal {proposal_id} is {proposal.status.value}")

        info = self._registry.get_validator(voter)
        if info is None or not info.is_active:
            raise AttestationError(f"Voter {voter} is not an active validator")
        if voter in proposal.voters:
            raise AttestationError(f"Voter {voter} already voted on {proposal_id}")

        proposal.voters.append(voter)
        proposal.total_participating_stake += info.stake_amount
        if in_favor:
            proposal.yes_stake += info.stake_amount
            proposal.yes_voters.append(voter)

        return proposal

    async def finalize_proposal(self, proposal_id: str) -> str:
        """Finalize a proposal that has reached supermajority + quorum.

        Delegates to the inner provider for actual attestation write.
        Returns the transaction ID from the inner provider.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise AttestationError(f"Proposal {proposal_id} not found")
        if proposal.status != ProposalStatus.PENDING:
            raise AttestationError(f"Proposal {proposal_id} is {proposal.status.value}")

        # Check quorum
        if len(proposal.voters) < self._consensus.min_quorum:
            raise AttestationError(
                f"Quorum not met: {len(proposal.voters)} < {self._consensus.min_quorum}"
            )

        # Check supermajority (2/3)
        if proposal.total_participating_stake > 0:
            ratio = proposal.yes_stake / proposal.total_participating_stake
        else:
            ratio = 0.0

        if ratio < SUPERMAJORITY_RATIO:
            raise AttestationError(
                f"Supermajority not met: {ratio:.2%} < 66.67%"
            )

        # Write through to inner provider
        record = self._pending_records[proposal_id]
        tx_id = await self._inner.submit(record)

        proposal.status = ProposalStatus.FINALIZED
        logger.info("Proposal %s finalized, tx=%s", proposal_id, tx_id)
        return tx_id

    # ── Challenge flow ────────────────────────────────────────────────

    def challenge(
        self,
        proposal_id: str,
        challenger: str,
        bond: float,
        disputed_leaf_hash: str,
    ) -> AttestationChallenge:
        """File a challenge against a finalized proposal."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise AttestationError(f"Proposal {proposal_id} not found")
        if proposal.status != ProposalStatus.FINALIZED:
            raise AttestationError("Can only challenge finalized proposals")

        challenge_id = str(uuid.uuid4())
        now = utcnow()

        ch = AttestationChallenge(
            challenge_id=challenge_id,
            proposal_id=proposal_id,
            challenger=challenger,
            bond=bond,
            disputed_leaf_hash=disputed_leaf_hash,
            filed_at=now,
            response_deadline=now + _CHALLENGE_PERIOD,
        )
        self._challenges[challenge_id] = ch
        logger.info("Challenge %s filed against proposal %s", challenge_id, proposal_id)
        return ch

    def resolve_challenge(
        self,
        challenge_id: str,
        challenger_wins: bool,
    ) -> dict[str, float]:
        """Resolve a pending challenge via the economics module."""
        ch = self._challenges.get(challenge_id)
        if ch is None:
            raise AttestationError(f"Challenge {challenge_id} not found")
        if ch.status != ChallengeStatus.PENDING:
            raise AttestationError(f"Challenge {challenge_id} already resolved")

        proposal = self._proposals[ch.proposal_id]

        settlements = self._economics.settle_challenge(
            challenge_id=challenge_id,
            yes_voters=proposal.yes_voters,
            challenger_address=ch.challenger,
            bond=ch.bond,
            challenger_wins=challenger_wins,
        )

        if challenger_wins:
            ch.status = ChallengeStatus.RESOLVED_CHALLENGER_WON
            proposal.status = ProposalStatus.SLASHED
        else:
            ch.status = ChallengeStatus.RESOLVED_CHALLENGER_LOST

        return settlements

    # ── Read-through to inner provider ────────────────────────────────

    def get_proposal(self, proposal_id: str) -> ConsensusProposal | None:
        return self._proposals.get(proposal_id)

    async def submit(self, record: AttestationRecord) -> str:
        raise AttestationError(
            "Direct submit not supported — use create_proposal → vote → finalize_proposal"
        )

    async def batch_submit(self, records: list[AttestationRecord]) -> list[str]:
        raise AttestationError(
            "Direct batch_submit not supported — use proposal flow"
        )

    async def verify(
        self, org_id_hash: str, period_start: datetime, period_end: datetime
    ) -> AttestationRecord | None:
        return await self._inner.verify(org_id_hash, period_start, period_end)

    async def get_latest(self, org_id_hash: str) -> AttestationRecord | None:
        return await self._inner.get_latest(org_id_hash)

    async def get_latest_nonce(self, org_id_hash: str) -> int:
        return await self._inner.get_latest_nonce(org_id_hash)

    async def get_org_hashes(self) -> list[str]:
        return await self._inner.get_org_hashes()
