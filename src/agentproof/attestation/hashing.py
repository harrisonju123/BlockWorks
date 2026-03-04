"""Off-chain hashing functions that produce the values committed on-chain.

Every function here uses hash_content() from pipeline/hasher.py to ensure
the same canonical serialization rules apply everywhere. Float rounding
to 6 decimal places prevents IEEE 754 drift between the write path
(callback) and the attestation path (batch job).
"""

from __future__ import annotations

import hashlib
import struct

from agentproof.attestation.merkle import MerkleTree
from agentproof.attestation.types import AttestationMetrics, AttestationRecord, TraceEvaluation
from agentproof.benchmarking.types import FitnessEntry
from agentproof.pipeline.hasher import hash_content


def keccak256(data: bytes) -> str:
    """Keccak-256 hash matching the EVM's native hash function.

    Used for anything that crosses the on-chain boundary: org_id hashing,
    chain linkage (prev_hash), and any future on-chain proof verification.
    Off-chain content hashing (metrics, fitness matrix, Merkle leaves) still
    uses SHA-256 via hash_content().
    """
    from Crypto.Hash import keccak

    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.hexdigest()


def compute_chain_hash(record: AttestationRecord) -> str:
    """Keccak-256 of ABI-packed attestation fields.

    Mirrors the Solidity _computeAttestationHash() exactly:
    keccak256(abi.encodePacked(orgIdHash, periodStart, periodEnd,
        metricsHash, benchmarkHash, merkleRoot, prevHash, nonce))

    Field types match the contract: bytes32 for hashes, uint40 for timestamps,
    uint64 for nonce. timestamp is excluded (matches the contract).
    """
    # Pack fields exactly as abi.encodePacked would
    packed = b""
    packed += bytes.fromhex(record.org_id_hash)  # bytes32
    packed += struct.pack(">Q", int(record.period_start.timestamp()))[3:]  # uint40 (low 5 bytes)
    packed += struct.pack(">Q", int(record.period_end.timestamp()))[3:]  # uint40 (low 5 bytes)
    packed += bytes.fromhex(record.metrics_hash)  # bytes32
    packed += bytes.fromhex(record.benchmark_hash)  # bytes32
    packed += bytes.fromhex(record.merkle_root)  # bytes32
    packed += bytes.fromhex(record.prev_hash)  # bytes32
    packed += struct.pack(">Q", record.nonce)  # uint64
    return keccak256(packed)


def hash_metrics(metrics: AttestationMetrics) -> str:
    """Canonical hash of period metrics.

    Field order is alphabetical by key (enforced by hash_content's
    sort_keys=True). Floats rounded to 6 decimals before serialization.
    """
    payload = {
        "failure_rate": round(metrics.failure_rate, 6),
        "model_distribution": metrics.model_distribution,
        "request_count": metrics.request_count,
        "total_spend": round(metrics.total_spend, 6),
        "waste_score": round(metrics.waste_score, 6),
    }
    return hash_content(payload)


def hash_fitness_matrix(entries: list[FitnessEntry]) -> str:
    """Hash the fitness matrix snapshot.

    Sorted by (task_type, model) internally so caller ordering doesn't
    affect the result.
    """
    sorted_entries = sorted(entries, key=lambda e: (e.task_type, e.model))
    payload = [
        {
            "avg_cost": round(e.avg_cost, 6),
            "avg_latency": round(e.avg_latency, 2),
            "avg_quality": round(e.avg_quality, 6),
            "model": e.model,
            "sample_size": e.sample_size,
            "task_type": e.task_type,
        }
        for e in sorted_entries
    ]
    return hash_content(payload)


def hash_org_id(org_id: str) -> str:
    """Keccak-256 pseudonym for an org, matching the on-chain orgIdHash.

    Uses keccak256 (not SHA-256) because this value is stored on-chain
    and must match what the Solidity contract computes.
    """
    return keccak256(org_id.encode("utf-8"))


def build_trace_leaf(evaluation: TraceEvaluation) -> str:
    """Canonical leaf hash for a single trace evaluation.

    The resulting hex string becomes one leaf in the attestation's
    Merkle tree.
    """
    payload = {
        "cost": round(evaluation.cost, 6),
        "model": evaluation.model,
        "quality_score": round(evaluation.quality_score, 6),
        "task_type": evaluation.task_type,
        "timestamp": evaluation.timestamp.isoformat(),
        "trace_id": evaluation.trace_id,
    }
    return hash_content(payload)


def build_merkle_root(evaluations: list[TraceEvaluation]) -> str:
    """Construct a Merkle tree from trace evaluations and return the root.

    Each evaluation is hashed to a canonical leaf, then the tree is built.
    Returns the hex root hash. For an empty list, returns the hash of an
    empty byte string (the EMPTY_LEAF sentinel).
    """
    if not evaluations:
        return MerkleTree([]).root

    leaf_data = [build_trace_leaf(e) for e in evaluations]
    tree = MerkleTree(leaf_data)
    return tree.root
