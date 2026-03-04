"""Shared test fixtures for the attestation subsystem."""

from __future__ import annotations

from datetime import datetime, timezone

from agentproof.attestation.types import AttestationRecord

NOW = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
PERIOD_START = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc)


def make_record(
    org_id_hash: str = "aa" * 32,
    nonce: int = 1,
    prev_hash: str = "0" * 64,
    period_start: datetime = PERIOD_START,
    period_end: datetime = PERIOD_END,
    metrics_hash: str = "dd" * 32,
    benchmark_hash: str = "bb" * 32,
    merkle_root: str = "cc" * 32,
    timestamp: datetime = NOW,
) -> AttestationRecord:
    return AttestationRecord(
        org_id_hash=org_id_hash,
        period_start=period_start,
        period_end=period_end,
        metrics_hash=metrics_hash,
        benchmark_hash=benchmark_hash,
        merkle_root=merkle_root,
        prev_hash=prev_hash,
        nonce=nonce,
        timestamp=timestamp,
    )
