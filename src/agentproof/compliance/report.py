"""Report generator — aggregates audit records into a compliance report.

Computes summary statistics, risk distribution, and a Merkle root over
all audit record hashes so the report can be verified for completeness
and tamper-resistance.
"""

from __future__ import annotations

from datetime import datetime

from agentproof.compliance.types import AuditRecord, AuditReport, RiskLevel
from agentproof.utils import utcnow
from agentproof.pipeline.hasher import hash_content


def generate_audit_report(
    records: list[AuditRecord],
    org_id: str,
    period_start: datetime,
    period_end: datetime,
) -> AuditReport:
    """Build a compliance report from a list of audit records.

    The attestation_hash is the Merkle root of all record hashes,
    providing a single value that commits to the entire audit trail.
    """
    # Deferred import to avoid circular import through attestation.__init__
    # (attestation -> benchmarking -> attestation.hashing cycle)
    from agentproof.attestation.merkle import MerkleTree

    total = len(records)

    # Human oversight percentage
    human_count = sum(1 for r in records if r.human_oversight_flag)
    human_pct = (human_count / total * 100.0) if total > 0 else 0.0

    # Risk distribution
    risk_dist: dict[str, int] = {level.value: 0 for level in RiskLevel}
    for record in records:
        risk_dist[record.risk_level.value] += 1

    # Merkle root over record hashes for tamper detection.
    # Each leaf is the record_hash (already a SHA-256 hex string),
    # so MerkleTree.leaf_hash will hash it again to form the leaf layer.
    leaf_data = [r.record_hash for r in records]
    tree = MerkleTree(leaf_data)
    attestation_hash = tree.root

    # Hash the org_id for the report (privacy)
    org_id_hashed = hash_content(org_id)

    return AuditReport(
        org_id=org_id_hashed,
        period_start=period_start,
        period_end=period_end,
        records=records,
        total_events=total,
        human_oversight_pct=round(human_pct, 2),
        risk_distribution=risk_dist,
        attestation_hash=attestation_hash,
        generated_at=utcnow(),
    )
