"""Governance subsystem — token-weighted proposal voting."""

from agentproof.governance.engine import GovernanceEngine
from agentproof.governance.types import (
    GovernanceConfig,
    Proposal,
    ProposalStatus,
    Vote,
    VoteSupport,
)

__all__ = [
    "GovernanceConfig",
    "GovernanceEngine",
    "Proposal",
    "ProposalStatus",
    "Vote",
    "VoteSupport",
]
