"""Metering and dispute resolution for cross-framework invocations.

Tracks token usage, cost, and latency per invocation. Provides a
simple in-memory dispute mechanism where callers can challenge
invocation outcomes. Metering records feed into the revenue sharing
module for settlement.
"""

from __future__ import annotations

import uuid

from blockthrough.interop.types import (
    DisputeRecord,
    DisputeStatus,
    InvocationRequest,
    InvocationResponse,
    MeteringRecord,
)
from blockthrough.pipeline.hasher import hash_content
from blockthrough.utils import utcnow


class DisputeNotFoundError(Exception):
    pass


class DisputeAlreadyResolvedError(Exception):
    pass


class MeteringStore:
    """In-memory store for metering records and disputes.

    Singleton pattern matching TrustRegistry and ChannelManager.
    Production would persist to TimescaleDB.
    """

    def __init__(self) -> None:
        self._records: dict[str, MeteringRecord] = {}
        self._disputes: dict[str, DisputeRecord] = {}

    def meter_invocation(
        self,
        request: InvocationRequest,
        response: InvocationResponse,
    ) -> MeteringRecord:
        """Record resource usage for a completed invocation.

        Estimates token count from the response payload size when
        actual token counts aren't available (stubs don't call LLMs).
        """
        # Rough token estimate: ~4 chars per token, based on the
        # combined size of params and result
        param_chars = len(str(request.params))
        result_chars = len(str(response.result))
        estimated_tokens = max(1, (param_chars + result_chars) // 4)

        record = MeteringRecord(
            invocation_id=response.request_id,
            caller_id=request.caller_agent_id,
            target_id=request.target_listing_id,
            tokens_used=estimated_tokens,
            cost=response.cost,
            latency_ms=response.latency_ms,
            timestamp=utcnow(),
        )

        self._records[record.invocation_id] = record
        return record

    def get_record(self, invocation_id: str) -> MeteringRecord | None:
        return self._records.get(invocation_id)

    def get_records_for(self, agent_id: str) -> list[MeteringRecord]:
        """Get all metering records where agent_id is caller or target."""
        return [
            r
            for r in self._records.values()
            if r.caller_id == agent_id or r.target_id == agent_id
        ]

    def open_dispute(
        self,
        invocation_id: str,
        initiator: str,
        reason: str,
        evidence_hash: str,
    ) -> DisputeRecord:
        """Open a dispute against a completed invocation.

        The evidence_hash should be the hash_content() of whatever
        evidence the initiator is presenting (e.g. the original
        request + unexpected response).
        """
        dispute_id = str(uuid.uuid4())
        now = utcnow()

        dispute = DisputeRecord(
            id=dispute_id,
            invocation_id=invocation_id,
            initiator=initiator,
            reason=reason,
            evidence_hash=evidence_hash,
            status=DisputeStatus.OPEN,
            opened_at=now,
        )

        self._disputes[dispute_id] = dispute
        return dispute

    def resolve_dispute(
        self,
        dispute_id: str,
        resolution: str,
        resolver: str,
    ) -> DisputeRecord:
        """Resolve an open dispute.

        Raises:
            DisputeNotFoundError: If dispute_id does not exist.
            DisputeAlreadyResolvedError: If the dispute is already resolved.
        """
        dispute = self._disputes.get(dispute_id)
        if dispute is None:
            raise DisputeNotFoundError(f"Dispute {dispute_id} not found")

        if dispute.status == DisputeStatus.RESOLVED:
            raise DisputeAlreadyResolvedError(
                f"Dispute {dispute_id} is already resolved"
            )

        now = utcnow()

        resolved = DisputeRecord(
            id=dispute.id,
            invocation_id=dispute.invocation_id,
            initiator=dispute.initiator,
            reason=dispute.reason,
            evidence_hash=dispute.evidence_hash,
            status=DisputeStatus.RESOLVED,
            resolution=resolution,
            resolver=resolver,
            opened_at=dispute.opened_at,
            resolved_at=now,
        )

        self._disputes[dispute_id] = resolved
        return resolved

    def get_dispute(self, dispute_id: str) -> DisputeRecord:
        """Get a dispute by ID.

        Raises:
            DisputeNotFoundError: If dispute_id does not exist.
        """
        dispute = self._disputes.get(dispute_id)
        if dispute is None:
            raise DisputeNotFoundError(f"Dispute {dispute_id} not found")
        return dispute

    def get_disputes_for(self, invocation_id: str) -> list[DisputeRecord]:
        """Get all disputes for a given invocation."""
        return [
            d
            for d in self._disputes.values()
            if d.invocation_id == invocation_id
        ]

    def reset(self) -> None:
        """Clear all state. Used by tests."""
        self._records.clear()
        self._disputes.clear()
