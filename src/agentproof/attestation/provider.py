"""Abstract base class for chain-agnostic attestation providers.

Every provider — in-memory, EVM L2, or future chain — implements this
interface so that upstream code never depends on chain-specific logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from agentproof.attestation.types import AttestationRecord


class AttestationError(Exception):
    """Raised when a provider rejects an attestation operation.

    Covers chain-linkage violations, nonce mismatches, duplicate
    submissions, and any other invariant the provider enforces.
    """


class AttestationProvider(ABC):
    """Chain-agnostic interface for submitting and querying attestations."""

    @abstractmethod
    async def submit(self, record: AttestationRecord) -> str:
        """Submit a single attestation. Returns the transaction/record ID."""

    @abstractmethod
    async def batch_submit(self, records: list[AttestationRecord]) -> list[str]:
        """Submit multiple attestations in one batch. Returns list of IDs."""

    @abstractmethod
    async def verify(
        self,
        org_id_hash: str,
        period_start: datetime,
        period_end: datetime,
    ) -> AttestationRecord | None:
        """Retrieve and verify an attestation for a specific period."""

    @abstractmethod
    async def get_latest(self, org_id_hash: str) -> AttestationRecord | None:
        """Get the most recent attestation for an org."""

    @abstractmethod
    async def get_latest_nonce(self, org_id_hash: str) -> int:
        """Get the nonce of the latest attestation (0 if none exist)."""
