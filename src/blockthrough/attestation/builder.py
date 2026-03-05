"""Orchestrates building a complete attestation record from DB state.

Queries the DB for the period's metrics, fitness matrix, and trace
evaluations, computes all hashes, builds the Merkle tree, and returns
a fully populated AttestationRecord ready for on-chain submission.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from blockthrough.attestation.hashing import (
    build_merkle_root,
    hash_fitness_matrix,
    hash_metrics,
    hash_org_id,
)
from blockthrough.attestation.types import (
    AttestationMetrics,
    AttestationRecord,
    TraceEvaluation,
)
from blockthrough.db.queries import get_attestation_metrics, get_trace_evaluations


# Sentinel for the first attestation in an org's chain
ZERO_HASH = "0" * 64


async def build_attestation(
    session: AsyncSession,
    org_id: str,
    period_start: datetime,
    period_end: datetime,
    prev_hash: str | None = None,
    nonce: int = 1,
) -> AttestationRecord:
    """Build a complete attestation from DB state for one org and period.

    Fetches metrics, fitness matrix entries, and trace evaluations via
    DB queries, then computes all cryptographic commitments.
    """
    metrics = await get_attestation_metrics(session, org_id, period_start, period_end)
    evaluations = await get_trace_evaluations(session, org_id, period_start, period_end)

    metrics_hash = hash_metrics(metrics)
    merkle_root = build_merkle_root(evaluations)
    org_id_hashed = hash_org_id(org_id)

    # Fitness matrix hash: we import here to avoid a circular dependency
    # at module level. The builder is the only consumer that needs both
    # the attestation hashing and the fitness query.
    from blockthrough.db.queries import get_fitness_matrix

    fitness_entries = await get_fitness_matrix(session, org_id=org_id)
    benchmark_hash = hash_fitness_matrix(fitness_entries)

    return AttestationRecord(
        org_id_hash=org_id_hashed,
        period_start=period_start,
        period_end=period_end,
        metrics_hash=metrics_hash,
        benchmark_hash=benchmark_hash,
        merkle_root=merkle_root,
        prev_hash=prev_hash or ZERO_HASH,
        nonce=nonce,
        timestamp=datetime.now(UTC),
    )
