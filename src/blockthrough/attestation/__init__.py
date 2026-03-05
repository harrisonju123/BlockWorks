"""Attestation subsystem — on-chain bridge and chain-agnostic provider layer.

Hashes off-chain AI operations data into Merkle trees and attestation
records that can be submitted to an L2 contract. All sensitive data
stays in TimescaleDB; only cryptographic commitments go on-chain.

Public API:
    AttestationProvider  -- abstract base class
    LocalProvider        -- in-memory provider for dev/testing
    AttestationRecord    -- canonical attestation data model
    AttestationMetrics   -- aggregate metrics for hashing
    TraceEvaluation      -- single Merkle leaf data
    MerkleTree           -- binary Merkle tree with sorted-pair hashing
    AttestationError     -- validation failures raised by providers
    create_provider      -- factory for constructing configured providers
"""

from blockthrough.attestation.factory import create_provider
from blockthrough.attestation.hashing import (
    build_merkle_root,
    build_trace_leaf,
    hash_fitness_matrix,
    hash_metrics,
    hash_org_id,
)
from blockthrough.attestation.local_provider import LocalProvider
from blockthrough.attestation.merkle import MerkleTree
from blockthrough.attestation.provider import AttestationError, AttestationProvider
from blockthrough.attestation.types import (
    AttestationMetrics,
    AttestationRecord,
    TraceEvaluation,
)

__all__ = [
    "AttestationError",
    "AttestationMetrics",
    "AttestationProvider",
    "AttestationRecord",
    "LocalProvider",
    "MerkleTree",
    "TraceEvaluation",
    "build_merkle_root",
    "build_trace_leaf",
    "hash_fitness_matrix",
    "create_provider",
    "hash_metrics",
    "hash_org_id",
]
