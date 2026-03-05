"""Pydantic models for the attestation subsystem.

These types represent the off-chain data structures that map to on-chain
attestation records. AttestationRecord mirrors the Solidity struct;
AttestationMetrics and TraceEvaluation hold the source data that gets
hashed into it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AttestationMetrics(BaseModel):
    """Aggregate metrics for one org over one attestation period.

    These fields are serialized to canonical JSON, then SHA-256 hashed
    to produce the metricsHash committed on-chain.
    """

    total_spend: float
    waste_score: float
    request_count: int
    failure_rate: float
    model_distribution: dict[str, int] = Field(default_factory=dict)


class TraceEvaluation(BaseModel):
    """One leaf of the Merkle tree: a single trace's evaluation data.

    Each trace_id maps to a set of LLM events. The leaf hash commits
    to the trace's aggregate cost, quality, and model usage so that
    inclusion in an attestation can be proven without revealing other
    traces.
    """

    trace_id: str
    model: str
    task_type: str
    cost: float
    quality_score: float
    timestamp: datetime


class AttestationRecord(BaseModel):
    """Off-chain mirror of the on-chain Attestation struct.

    All hash fields are 64-character hex strings (SHA-256 output).
    The nonce is assigned at build time as a monotonic counter per org.
    """

    org_id_hash: str
    period_start: datetime
    period_end: datetime
    metrics_hash: str
    benchmark_hash: str
    merkle_root: str
    prev_hash: str
    nonce: int
    timestamp: datetime
