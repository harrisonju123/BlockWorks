# ADR-004 --- On-Chain Attestation Protocol

**Date:** 2026-03-03
**Status:** Proposed
**Authors:** Principal Architect
**Scope:** Initiative 2A -- attestation schema, chain selection, smart contract design, off-chain bridge, and chain-agnostic abstraction
**Dependencies:** Phase 1 frozen interfaces (ADR-003 Section 6)

---

## Context

Phase 1 delivers benchmarking, waste detection, smart routing, MCP tracing, and alerts. Phase 2 anchors those results on-chain so organizations can produce cryptographic proof that their AI operations were evaluated against known benchmarks and that cost/quality claims are tamper-evident.

The attestation layer does NOT store raw data on-chain. It stores hashes, Merkle roots, and chain-linkage pointers. All sensitive data remains in TimescaleDB. The on-chain record is a lightweight, verifiable commitment that the off-chain data existed in a specific state at a specific time.

This document is the architecture plan for initiative 2A. It covers all six sub-tasks (2A-1 through 2A-6) and provides the contract interfaces, cost estimates, and implementation roadmap that the web3 engineer needs to begin work.

---

## 1. Attestation Schema Design (2A-1)

### On-Chain Record

Each attestation represents one organization's AI operations over one time period. The on-chain footprint is minimal: five 32-byte hashes plus two uint40 timestamps and a uint64 nonce.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

struct Attestation {
    bytes32 orgIdHash;        // keccak256(org_id) -- pseudonymous
    uint40  periodStart;      // unix timestamp (fits until year 36812)
    uint40  periodEnd;        // unix timestamp
    bytes32 metricsHash;      // SHA-256 of canonical metrics JSON
    bytes32 benchmarkHash;    // SHA-256 of fitness matrix snapshot
    bytes32 merkleRoot;       // root of trace evaluation Merkle tree
    bytes32 prevHash;         // hash of this org's previous attestation (chain linkage)
    uint64  nonce;            // monotonic counter per org, prevents replay
}
```

**Total on-chain storage per attestation:** 7 x 32 bytes + 2 x 5 bytes + 8 bytes = 242 bytes. Rounded to 256 bytes (8 EVM words) for slot alignment.

### Off-Chain Data That Maps to Each Hash

#### `orgIdHash`

```python
import hashlib

def compute_org_id_hash(org_id: str) -> bytes:
    """Pseudonymous org identifier. One-way: cannot recover org_id from hash."""
    return hashlib.sha256(org_id.encode("utf-8")).digest()
```

The org_id itself never touches the chain. Verification requires knowing the org_id and re-hashing.

#### `metricsHash`

The metrics hash commits to a canonical JSON representation of the period's aggregate numbers. This uses the same `hash_content` function from `src/agentproof/pipeline/hasher.py` to guarantee deterministic serialization.

```python
from agentproof.pipeline.hasher import hash_content

def compute_metrics_hash(
    total_spend: float,
    waste_score: float,
    request_count: int,
    failure_rate: float,
    model_distribution: dict[str, int],  # model_name -> request_count
) -> str:
    """Canonical hash of period metrics. Field order is alphabetical by key."""
    payload = {
        "failure_rate": round(failure_rate, 6),
        "model_distribution": model_distribution,
        "request_count": request_count,
        "total_spend": round(total_spend, 6),
        "waste_score": round(waste_score, 6),
    }
    return hash_content(payload)
```

**Rounding rule:** floats are rounded to 6 decimal places before hashing. This prevents floating-point drift between the write path (callback) and the attestation path (batch job) from breaking hash equality.

#### `benchmarkHash`

Commits to the fitness matrix state at attestation time. The matrix is a list of `FitnessEntry` objects (defined in `src/agentproof/benchmarking/types.py`) serialized in a canonical order.

```python
from agentproof.benchmarking.types import FitnessEntry
from agentproof.pipeline.hasher import hash_content

def compute_benchmark_hash(entries: list[FitnessEntry]) -> str:
    """Hash the fitness matrix snapshot. Sorted by (task_type, model) for determinism."""
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
```

#### `merkleRoot`

The Merkle root anchors every individual trace evaluation from the period. See Section 5 for the full tree construction algorithm.

#### `prevHash`

The previous attestation hash for this org creates a per-org audit chain. For the first attestation, `prevHash` is `bytes32(0)`. Computed as:

```python
def compute_attestation_hash(attestation: dict) -> str:
    """Hash of a complete attestation record, used as prevHash in the next one."""
    return hash_content({
        "benchmark_hash": attestation["benchmark_hash"],
        "merkle_root": attestation["merkle_root"],
        "metrics_hash": attestation["metrics_hash"],
        "nonce": attestation["nonce"],
        "org_id_hash": attestation["org_id_hash"],
        "period_end": attestation["period_end"],
        "period_start": attestation["period_start"],
    })
```

### Off-Chain Storage

The full attestation record (including all unhashed source data) is stored in a new `attestations` table alongside a `trace_evaluations` table that holds the Merkle leaves.

```sql
CREATE TABLE attestations (
    id              UUID NOT NULL PRIMARY KEY,
    org_id          TEXT NOT NULL,
    org_id_hash     BYTEA NOT NULL,           -- 32 bytes, the on-chain pseudonym
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    metrics_hash    TEXT NOT NULL,             -- hex-encoded SHA-256
    benchmark_hash  TEXT NOT NULL,
    merkle_root     TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,             -- hex 0x00..00 for first attestation
    nonce           BIGINT NOT NULL,
    tx_hash         TEXT,                      -- L2 transaction hash, NULL until submitted
    block_number    BIGINT,                    -- L2 block, NULL until confirmed
    chain_id        INTEGER,                   -- EIP-155 chain ID
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'submitted', 'confirmed', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (org_id, nonce)
);

CREATE INDEX idx_attestation_org ON attestations (org_id, nonce DESC);
CREATE INDEX idx_attestation_status ON attestations (status) WHERE status != 'confirmed';

CREATE TABLE trace_evaluations (
    id              UUID NOT NULL PRIMARY KEY,
    attestation_id  UUID NOT NULL REFERENCES attestations(id),
    trace_id        TEXT NOT NULL,
    evaluation_hash TEXT NOT NULL,             -- SHA-256 of trace evaluation data
    leaf_index      INTEGER NOT NULL,          -- position in Merkle tree
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_trace_eval_attestation ON trace_evaluations (attestation_id, leaf_index);
CREATE INDEX idx_trace_eval_trace ON trace_evaluations (trace_id);
```

---

## 2. EAS vs Custom Contracts (2A-2)

### Option A: Ethereum Attestation Service (EAS)

EAS is a general-purpose attestation protocol deployed on Ethereum mainnet and all major L2s. It provides a schema registry, an attestation contract, and SDK tooling.

**Pros:**
- Schema registry is already deployed and indexed. No need to deploy or maintain our own registry contract.
- Existing subgraph and indexer infrastructure. Third parties can discover and verify AgentProof attestations without knowing our contract address.
- Composability: other protocols can reference our attestations by UID.
- Revocation support built in (useful if an attestation is found to be based on corrupted data).
- Active maintenance by the EAS team; security audits already completed.

**Cons:**
- Schema flexibility is limited to ABI-encoded tuples. Our `prevHash` chain linkage and `nonce` replay protection must be enforced at the application layer, not the contract layer. EAS does not natively enforce per-schema sequencing.
- Gas overhead: EAS wraps our data in its own storage struct (~30% more gas than a purpose-built contract).
- Dependency on EAS contract upgrades. If EAS changes its storage layout or indexing, our verification code must adapt.
- No native batch attestation. Submitting 100 org attestations requires 100 separate `attest()` calls (or a multicall wrapper).

### Option B: Custom Smart Contract

A purpose-built `AgentProofAttestation` contract with exactly the storage and access patterns we need.

**Pros:**
- Full control over storage layout, gas optimization, and batch operations.
- Native enforcement of per-org chain linkage (the contract rejects an attestation if `prevHash` does not match the stored latest).
- Batch attestation in a single transaction (100 orgs = 1 tx).
- No external dependency. Upgrade schedule is entirely ours.

**Cons:**
- Requires a professional audit before mainnet/L2 deployment. Estimated cost: $15k-$30k for a contract of this complexity.
- More code to maintain. We own the indexer, the verification library, and the contract itself.
- No automatic discoverability. Third parties must know our contract address.

### Option C: Hybrid -- Custom Contract with EAS Registration

Deploy a custom contract for storage and batch efficiency, but register our attestation schema in the EAS schema registry. This gives us discoverability without the gas overhead.

**Recommendation: Option B (Custom Contract) with EAS schema registration as a Phase 3 enhancement.**

Rationale:
1. Batch attestation is a hard requirement. At 100 orgs with daily attestations, calling EAS 100 times per day is wasteful. A single `batchAttest` call saves ~40% on gas.
2. Per-org chain linkage must be enforced at the contract level. Application-layer enforcement is a security gap: a compromised attestation service could skip entries in the chain.
3. The contract is small (~200 lines of Solidity). Audit cost is at the low end of the range.
4. EAS schema registration can be added later without modifying the core contract. It is a one-time registration call that points to our contract.

---

## 3. L2 Chain Selection (2A-3)

### Comparison Matrix

All costs estimated as of March 2026 using 256 bytes calldata per single attestation and 256 * 100 = 25,600 bytes for a batch of 100.

| Criterion | Base | Arbitrum | Optimism |
|-----------|------|----------|----------|
| **Gas price (median, gwei)** | 0.005 | 0.01 | 0.005 |
| **Single attestation cost** | ~$0.005 | ~$0.01 | ~$0.005 |
| **Batch (100 orgs) cost** | ~$0.08 | ~$0.15 | ~$0.08 |
| **Monthly cost (100 orgs, daily)** | ~$2.40 | ~$4.50 | ~$2.40 |
| **Foundry support** | Full | Full | Full |
| **Hardhat support** | Full | Full | Full |
| **Block explorer** | Basescan | Arbiscan | Optimistic Etherscan |
| **RPC providers** | Alchemy, Infura, QuickNode, Coinbase | Alchemy, Infura, QuickNode | Alchemy, Infura, QuickNode |
| **Attestation ecosystem** | EAS deployed, Coinbase Verifications, Worldcoin | EAS deployed | EAS deployed, AttestationStation |
| **DeFi composability** | Strong (Coinbase ecosystem) | Strongest (highest TVL) | Moderate |
| **Native bridge** | Yes (Coinbase Bridge) | Yes (Arbitrum Bridge) | Yes (OP Bridge) |
| **Cross-chain messaging** | CCIP, LayerZero | CCIP, LayerZero, Hyperlane | CCIP, LayerZero |
| **Block time** | 2s | 0.25s | 2s |

### Cost Derivation

L2 transaction cost = L2 execution gas + L1 data posting cost.

For a single attestation (`attest` call with 256 bytes of calldata):
- L2 execution: ~80,000 gas (SSTORE for new attestation + SLOAD for prev check)
- L1 data (post-EIP-4844 blobs): 256 bytes * ~0.01 gwei/byte = negligible on all three chains
- At 0.005 gwei and ETH at $3,200: 80,000 * 0.005 * 10^-9 * 3,200 = ~$0.0013

For a batch of 100 (`batchAttest`):
- L2 execution: ~50,000 per attestation (amortized SLOAD overhead) * 100 = 5,000,000 gas
- L1 data: 25,600 bytes in calldata
- At 0.005 gwei: 5,000,000 * 0.005 * 10^-9 * 3,200 = ~$0.08

### Recommendation: Base

1. **Cost parity with Optimism**, both significantly cheaper than Arbitrum for our use case.
2. **Ecosystem fit.** Base has the strongest attestation ecosystem (EAS + Coinbase Verifications). If we later integrate EAS schema registration (Phase 3), the tooling is native.
3. **RPC reliability.** Coinbase operates the sequencer and provides free-tier RPC endpoints. Alchemy and QuickNode provide redundant access.
4. **Onchain identity.** Coinbase Verified credentials on Base could complement our org pseudonym scheme if customers want to link attestations to verified corporate identities.
5. **Developer experience.** Foundry + Basescan + Coinbase Wallet integration is well-documented and actively maintained.

**Fallback:** If Base experiences sustained congestion or governance issues, Optimism is the direct substitute with identical cost characteristics and OP Stack compatibility. The chain-agnostic abstraction layer (Section 6) makes this swap a configuration change.

---

## 4. Smart Contract Design (2A-4)

### Interface

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title AgentProof Attestation Registry
/// @notice Stores cryptographic commitments to off-chain AI operations data.
///         No raw data touches the chain -- only hashes and Merkle roots.
contract AgentProofAttestation is Ownable {

    // ---------------------------------------------------------------
    //  Types
    // ---------------------------------------------------------------

    struct Attestation {
        bytes32 orgIdHash;
        uint40  periodStart;
        uint40  periodEnd;
        bytes32 metricsHash;
        bytes32 benchmarkHash;
        bytes32 merkleRoot;
        bytes32 prevHash;
        uint64  nonce;
    }

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    /// @dev orgIdHash -> nonce -> Attestation
    mapping(bytes32 => mapping(uint64 => Attestation)) public attestations;

    /// @dev orgIdHash -> latest nonce (0 means no attestations yet)
    mapping(bytes32 => uint64) public latestNonce;

    /// @dev Addresses authorized to submit attestations
    mapping(address => bool) public attestors;

    // ---------------------------------------------------------------
    //  Events
    // ---------------------------------------------------------------

    event AttestationSubmitted(
        bytes32 indexed orgIdHash,
        uint64  indexed nonce,
        bytes32 merkleRoot,
        uint40  periodStart,
        uint40  periodEnd
    );

    event AttestorUpdated(address indexed attestor, bool authorized);

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error Unauthorized();
    error InvalidPrevHash(bytes32 expected, bytes32 provided);
    error InvalidPeriod();
    error NonceNotSequential(uint64 expected, uint64 provided);

    // ---------------------------------------------------------------
    //  Modifiers
    // ---------------------------------------------------------------

    modifier onlyAttestor() {
        if (!attestors[msg.sender]) revert Unauthorized();
        _;
    }

    // ---------------------------------------------------------------
    //  Constructor
    // ---------------------------------------------------------------

    constructor(address initialOwner) Ownable(initialOwner) {
        attestors[initialOwner] = true;
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    /// @notice Grant or revoke attestation submission rights.
    function setAttestor(address attestor, bool authorized) external onlyOwner {
        attestors[attestor] = authorized;
        emit AttestorUpdated(attestor, authorized);
    }

    // ---------------------------------------------------------------
    //  Core: Submit
    // ---------------------------------------------------------------

    /// @notice Submit a single attestation. Enforces chain linkage and nonce ordering.
    function attest(
        bytes32 orgIdHash,
        uint40  periodStart,
        uint40  periodEnd,
        bytes32 metricsHash,
        bytes32 benchmarkHash,
        bytes32 merkleRoot,
        bytes32 prevHash
    ) external onlyAttestor {
        _attest(orgIdHash, periodStart, periodEnd, metricsHash, benchmarkHash, merkleRoot, prevHash);
    }

    /// @notice Submit attestations for multiple orgs in a single transaction.
    /// @dev Arrays must be equal length. Saves ~30k gas per attestation vs individual calls.
    function batchAttest(
        bytes32[] calldata orgIdHashes,
        uint40[]  calldata periodStarts,
        uint40[]  calldata periodEnds,
        bytes32[] calldata metricsHashes,
        bytes32[] calldata benchmarkHashes,
        bytes32[] calldata merkleRoots,
        bytes32[] calldata prevHashes
    ) external onlyAttestor {
        uint256 len = orgIdHashes.length;
        require(
            len == periodStarts.length &&
            len == periodEnds.length &&
            len == metricsHashes.length &&
            len == benchmarkHashes.length &&
            len == merkleRoots.length &&
            len == prevHashes.length,
            "Array length mismatch"
        );

        for (uint256 i = 0; i < len;) {
            _attest(
                orgIdHashes[i], periodStarts[i], periodEnds[i],
                metricsHashes[i], benchmarkHashes[i], merkleRoots[i], prevHashes[i]
            );
            unchecked { ++i; }
        }
    }

    // ---------------------------------------------------------------
    //  Core: Verify
    // ---------------------------------------------------------------

    /// @notice Retrieve an attestation by org and nonce.
    function verify(
        bytes32 orgIdHash,
        uint64  nonce
    ) external view returns (Attestation memory) {
        return attestations[orgIdHash][nonce];
    }

    /// @notice Retrieve the most recent attestation for an org.
    function getLatest(bytes32 orgIdHash) external view returns (Attestation memory) {
        uint64 nonce = latestNonce[orgIdHash];
        return attestations[orgIdHash][nonce];
    }

    /// @notice Retrieve attestations for an org within a time range.
    /// @dev Iterates backward from latest nonce. Caller should bound maxResults
    ///      to avoid gas exhaustion on view calls.
    function getByPeriod(
        bytes32 orgIdHash,
        uint40  start,
        uint40  end,
        uint64  maxResults
    ) external view returns (Attestation[] memory) {
        uint64 current = latestNonce[orgIdHash];
        Attestation[] memory results = new Attestation[](maxResults);
        uint64 count = 0;

        while (current > 0 && count < maxResults) {
            Attestation storage a = attestations[orgIdHash][current];
            if (a.periodEnd <= end && a.periodStart >= start) {
                results[count] = a;
                unchecked { ++count; }
            }
            // Stop searching if we've gone past the start of the range
            if (a.periodStart < start) break;
            unchecked { --current; }
        }

        // Trim the array to actual size
        assembly {
            mstore(results, count)
        }
        return results;
    }

    // ---------------------------------------------------------------
    //  Internal
    // ---------------------------------------------------------------

    function _attest(
        bytes32 orgIdHash,
        uint40  periodStart,
        uint40  periodEnd,
        bytes32 metricsHash,
        bytes32 benchmarkHash,
        bytes32 merkleRoot,
        bytes32 prevHash
    ) internal {
        if (periodEnd <= periodStart) revert InvalidPeriod();

        uint64 expectedNonce = latestNonce[orgIdHash] + 1;

        // Chain linkage: prevHash must match the hash of the previous attestation
        if (expectedNonce == 1) {
            // First attestation for this org: prevHash must be zero
            if (prevHash != bytes32(0)) {
                revert InvalidPrevHash(bytes32(0), prevHash);
            }
        } else {
            bytes32 computedPrev = _computeAttestationHash(
                attestations[orgIdHash][expectedNonce - 1]
            );
            if (prevHash != computedPrev) {
                revert InvalidPrevHash(computedPrev, prevHash);
            }
        }

        Attestation memory a = Attestation({
            orgIdHash: orgIdHash,
            periodStart: periodStart,
            periodEnd: periodEnd,
            metricsHash: metricsHash,
            benchmarkHash: benchmarkHash,
            merkleRoot: merkleRoot,
            prevHash: prevHash,
            nonce: expectedNonce
        });

        attestations[orgIdHash][expectedNonce] = a;
        latestNonce[orgIdHash] = expectedNonce;

        emit AttestationSubmitted(orgIdHash, expectedNonce, merkleRoot, periodStart, periodEnd);
    }

    /// @dev Compute the hash of an attestation for chain linkage.
    ///      Uses abi.encodePacked for gas efficiency. The off-chain code
    ///      must replicate this exact encoding.
    function _computeAttestationHash(
        Attestation storage a
    ) internal view returns (bytes32) {
        return keccak256(abi.encodePacked(
            a.orgIdHash,
            a.periodStart,
            a.periodEnd,
            a.metricsHash,
            a.benchmarkHash,
            a.merkleRoot,
            a.prevHash,
            a.nonce
        ));
    }
}
```

### Gas Optimization Notes

1. **`unchecked` arithmetic** in the batch loop and nonce iteration. Overflow is impossible because nonce is uint64 and we increment by 1.
2. **`calldata` arrays** in `batchAttest` instead of `memory`. Saves ~3 gas per byte for large batches.
3. **Storage packing.** `periodStart` (uint40) and `periodEnd` (uint40) pack into a single 32-byte slot alongside `nonce` (uint64). Total struct storage: 8 slots.
4. **No string storage.** Everything is bytes32. String-to-bytes32 conversion happens off-chain.
5. **Assembly trim** in `getByPeriod` avoids allocating a new array for the return value.

### Access Control

- **Owner:** The deployer (a multisig in production). Can grant/revoke attestor roles.
- **Attestors:** Addresses authorized to call `attest` and `batchAttest`. In production, this is the AgentProof attestation service's hot wallet. The hot wallet holds minimal ETH for gas and has no other permissions.
- **Readers:** Anyone. All `verify`, `getLatest`, and `getByPeriod` functions are public view.

---

## 5. Off-Chain to On-Chain Bridge (2A-5)

### Merkle Tree Construction

The Merkle tree anchors individual trace evaluations to a single on-chain root. Each leaf is the hash of one trace's evaluation data. This allows proving that a specific trace was included in an attestation without revealing any other trace.

#### Leaf Construction

Each leaf represents one trace (a sequence of LLM events sharing a `trace_id`). The leaf hash commits to the trace's aggregate metrics.

```python
from agentproof.pipeline.hasher import hash_content


def compute_trace_leaf(
    trace_id: str,
    total_cost: float,
    total_tokens: int,
    event_count: int,
    models_used: list[str],
    task_types: list[str],
    waste_flags: list[str],
) -> str:
    """Compute the Merkle leaf hash for a single trace evaluation."""
    payload = {
        "event_count": event_count,
        "models_used": sorted(models_used),
        "task_types": sorted(task_types),
        "total_cost": round(total_cost, 6),
        "total_tokens": total_tokens,
        "trace_id": trace_id,
        "waste_flags": sorted(waste_flags),
    }
    return hash_content(payload)
```

#### Tree Construction

Standard binary Merkle tree with SHA-256. If the leaf count is not a power of two, pad with the hash of an empty byte string.

```python
import hashlib
from typing import Sequence


EMPTY_LEAF = hashlib.sha256(b"").hexdigest()


def build_merkle_tree(leaves: Sequence[str]) -> tuple[str, list[list[str]]]:
    """Build a binary Merkle tree from hex-encoded leaf hashes.

    Returns:
        (root_hash, tree_layers) where tree_layers[0] = leaves, tree_layers[-1] = [root].
        The tree_layers structure is retained so we can generate proofs.
    """
    if not leaves:
        return EMPTY_LEAF, [[EMPTY_LEAF]]

    # Pad to next power of 2
    padded = list(leaves)
    next_pow2 = 1
    while next_pow2 < len(padded):
        next_pow2 <<= 1
    while len(padded) < next_pow2:
        padded.append(EMPTY_LEAF)

    layers: list[list[str]] = [padded]

    current = padded
    while len(current) > 1:
        next_layer = []
        for i in range(0, len(current), 2):
            # Sort pair before hashing to make the tree order-independent
            # within each pair. This simplifies proof verification.
            left, right = current[i], current[i + 1]
            if left > right:
                left, right = right, left
            combined = hashlib.sha256(
                bytes.fromhex(left) + bytes.fromhex(right)
            ).hexdigest()
            next_layer.append(combined)
        layers.append(next_layer)
        current = next_layer

    return current[0], layers


def generate_proof(leaf_index: int, layers: list[list[str]]) -> list[tuple[str, str]]:
    """Generate a Merkle inclusion proof for the leaf at the given index.

    Returns a list of (sibling_hash, position) tuples where position is 'left' or 'right'.
    """
    proof: list[tuple[str, str]] = []
    idx = leaf_index

    for layer in layers[:-1]:  # skip the root layer
        if idx % 2 == 0:
            sibling_idx = idx + 1
            position = "right"
        else:
            sibling_idx = idx - 1
            position = "left"

        if sibling_idx < len(layer):
            proof.append((layer[sibling_idx], position))
        idx //= 2

    return proof


def verify_proof(
    leaf_hash: str,
    proof: list[tuple[str, str]],
    expected_root: str,
) -> bool:
    """Verify that a leaf is included in the Merkle tree with the given root."""
    current = leaf_hash

    for sibling, position in proof:
        if position == "left":
            left, right = sibling, current
        else:
            left, right = current, sibling

        # Same sort-before-hash as tree construction
        if left > right:
            left, right = right, left

        current = hashlib.sha256(
            bytes.fromhex(left) + bytes.fromhex(right)
        ).hexdigest()

    return current == expected_root
```

### Data Flow: DB to L2 Transaction

```
                    AgentProof DB (TimescaleDB)
                            |
                    [1] Daily batch job
                            |
                            v
                    Attestation Service (Python)
                    |       |       |       |
              [2] Query   [3] Query  [4] Query  [5] Query
              traces      metrics    fitness    prev attestation
                    |       |       |       |
                    v       v       v       v
              [6] Build Merkle tree from trace leaves
              [7] Compute metrics hash
              [8] Compute benchmark hash
              [9] Look up prevHash from latest confirmed attestation
                            |
                            v
              [10] Construct attestation record
              [11] Store in attestations table (status='pending')
              [12] Store trace leaves in trace_evaluations table
                            |
                            v
              [13] Submit to L2 via AttestationProvider.submit()
              [14] Wait for tx confirmation
              [15] Update attestation record (status='confirmed', tx_hash, block_number)
```

**Step-by-step:**

1. A daily cron job (or manual trigger) initiates attestation for each org.
2. Query all traces in the period: `SELECT trace_id, SUM(estimated_cost), ...  FROM llm_events WHERE org_id = :org_id AND created_at BETWEEN :start AND :end GROUP BY trace_id`.
3. Query period aggregate metrics from `daily_summary` continuous aggregate.
4. Query fitness matrix snapshot from `fitness_matrix` continuous aggregate.
5. Query the most recent confirmed attestation for this org from the `attestations` table.
6. Compute a leaf hash for each trace. Build the Merkle tree. Extract the root.
7-9. Compute the metrics hash, benchmark hash, and prevHash using the functions defined in Section 1.
10. Assemble the full attestation record.
11-12. Persist the off-chain record and all Merkle leaves.
13. Call `AttestationProvider.submit()` which encodes the call and sends the L2 transaction.
14. Poll for confirmation (or use a webhook from the RPC provider).
15. Update the off-chain record with the transaction receipt.

### Verification Flow (Third-Party Auditor)

A third party wanting to verify a claim about a specific trace:

1. Request the trace evaluation data and Merkle proof from the AgentProof API.
2. Re-compute the leaf hash from the evaluation data.
3. Verify the Merkle proof against the on-chain `merkleRoot` (call `getLatest` or `verify` on the contract).
4. Optionally verify the chain linkage by checking `prevHash` against the prior attestation.

---

## 6. Chain-Agnostic Abstraction (2A-6)

### Interface

```python
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class AttestationRecord:
    """The data structure submitted to and retrieved from the chain."""

    org_id_hash: bytes        # 32 bytes
    period_start: int         # unix timestamp
    period_end: int           # unix timestamp
    metrics_hash: bytes       # 32 bytes
    benchmark_hash: bytes     # 32 bytes
    merkle_root: bytes        # 32 bytes
    prev_hash: bytes          # 32 bytes
    nonce: int


@dataclass(frozen=True)
class SubmitResult:
    """Result of submitting an attestation to the chain."""

    tx_hash: str
    chain_id: int
    success: bool
    error: str | None = None
    block_number: int | None = None


class AttestationProvider(abc.ABC):
    """Chain-agnostic interface for attestation operations.

    Implementations handle chain-specific encoding, signing, and submission.
    Upstream code (the attestation service) never imports web3, ethers, or
    any chain SDK directly.
    """

    @abc.abstractmethod
    async def submit(self, record: AttestationRecord) -> SubmitResult:
        """Submit a single attestation to the chain."""
        ...

    @abc.abstractmethod
    async def batch_submit(
        self, records: Sequence[AttestationRecord]
    ) -> list[SubmitResult]:
        """Submit multiple attestations in a single transaction."""
        ...

    @abc.abstractmethod
    async def verify(
        self, org_id_hash: bytes, nonce: int
    ) -> AttestationRecord | None:
        """Retrieve an attestation by org and nonce. Returns None if not found."""
        ...

    @abc.abstractmethod
    async def get_latest(self, org_id_hash: bytes) -> AttestationRecord | None:
        """Retrieve the most recent attestation for an org."""
        ...

    @abc.abstractmethod
    async def get_latest_nonce(self, org_id_hash: bytes) -> int:
        """Return the latest nonce for an org. 0 means no attestations."""
        ...
```

### Implementation: EVMProvider (Base / Arbitrum / Optimism)

```python
from web3 import AsyncWeb3
from web3.contract import AsyncContract

from agentproof.attestation.provider import (
    AttestationProvider,
    AttestationRecord,
    SubmitResult,
)


class EVMProvider(AttestationProvider):
    """AttestationProvider backed by an EVM-compatible L2 contract."""

    def __init__(
        self,
        rpc_url: str,
        contract_address: str,
        contract_abi: list[dict],
        private_key: str,
        chain_id: int,
    ) -> None:
        self._w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
        self._contract: AsyncContract = self._w3.eth.contract(
            address=contract_address,
            abi=contract_abi,
        )
        self._account = self._w3.eth.account.from_key(private_key)
        self._chain_id = chain_id

    async def submit(self, record: AttestationRecord) -> SubmitResult:
        try:
            tx = await self._contract.functions.attest(
                record.org_id_hash,
                record.period_start,
                record.period_end,
                record.metrics_hash,
                record.benchmark_hash,
                record.merkle_root,
                record.prev_hash,
            ).build_transaction({
                "from": self._account.address,
                "nonce": await self._w3.eth.get_transaction_count(
                    self._account.address
                ),
                "chainId": self._chain_id,
            })

            signed = self._account.sign_transaction(tx)
            tx_hash = await self._w3.eth.send_raw_transaction(
                signed.raw_transaction
            )
            receipt = await self._w3.eth.wait_for_transaction_receipt(tx_hash)

            return SubmitResult(
                tx_hash=receipt["transactionHash"].hex(),
                chain_id=self._chain_id,
                success=receipt["status"] == 1,
                block_number=receipt["blockNumber"],
            )
        except Exception as e:
            return SubmitResult(
                tx_hash="",
                chain_id=self._chain_id,
                success=False,
                error=str(e),
            )

    async def batch_submit(
        self, records: Sequence[AttestationRecord]
    ) -> list[SubmitResult]:
        """Submit all records in a single batchAttest call."""
        try:
            tx = await self._contract.functions.batchAttest(
                [r.org_id_hash for r in records],
                [r.period_start for r in records],
                [r.period_end for r in records],
                [r.metrics_hash for r in records],
                [r.benchmark_hash for r in records],
                [r.merkle_root for r in records],
                [r.prev_hash for r in records],
            ).build_transaction({
                "from": self._account.address,
                "nonce": await self._w3.eth.get_transaction_count(
                    self._account.address
                ),
                "chainId": self._chain_id,
            })

            signed = self._account.sign_transaction(tx)
            tx_hash = await self._w3.eth.send_raw_transaction(
                signed.raw_transaction
            )
            receipt = await self._w3.eth.wait_for_transaction_receipt(tx_hash)

            result = SubmitResult(
                tx_hash=receipt["transactionHash"].hex(),
                chain_id=self._chain_id,
                success=receipt["status"] == 1,
                block_number=receipt["blockNumber"],
            )
            # All records share the same tx, so return the same result for each
            return [result] * len(records)
        except Exception as e:
            error_result = SubmitResult(
                tx_hash="",
                chain_id=self._chain_id,
                success=False,
                error=str(e),
            )
            return [error_result] * len(records)

    async def verify(
        self, org_id_hash: bytes, nonce: int
    ) -> AttestationRecord | None:
        raw = await self._contract.functions.verify(org_id_hash, nonce).call()
        if raw[0] == b"\x00" * 32:  # empty orgIdHash means not found
            return None
        return self._decode_attestation(raw)

    async def get_latest(self, org_id_hash: bytes) -> AttestationRecord | None:
        raw = await self._contract.functions.getLatest(org_id_hash).call()
        if raw[0] == b"\x00" * 32:
            return None
        return self._decode_attestation(raw)

    async def get_latest_nonce(self, org_id_hash: bytes) -> int:
        return await self._contract.functions.latestNonce(org_id_hash).call()

    @staticmethod
    def _decode_attestation(raw: tuple) -> AttestationRecord:
        return AttestationRecord(
            org_id_hash=raw[0],
            period_start=raw[1],
            period_end=raw[2],
            metrics_hash=raw[3],
            benchmark_hash=raw[4],
            merkle_root=raw[5],
            prev_hash=raw[6],
            nonce=raw[7],
        )
```

### Implementation: LocalProvider (Development and Testing)

```python
from collections import defaultdict
from typing import Sequence

from agentproof.attestation.provider import (
    AttestationProvider,
    AttestationRecord,
    SubmitResult,
)


class LocalProvider(AttestationProvider):
    """In-memory attestation provider for development and testing.

    Replicates the contract's chain linkage and nonce enforcement
    without requiring an L2 node or test network.
    """

    def __init__(self) -> None:
        # org_id_hash -> nonce -> AttestationRecord
        self._store: dict[bytes, dict[int, AttestationRecord]] = defaultdict(dict)
        self._latest_nonce: dict[bytes, int] = defaultdict(int)
        self._tx_counter = 0

    async def submit(self, record: AttestationRecord) -> SubmitResult:
        expected_nonce = self._latest_nonce[record.org_id_hash] + 1

        self._store[record.org_id_hash][expected_nonce] = AttestationRecord(
            org_id_hash=record.org_id_hash,
            period_start=record.period_start,
            period_end=record.period_end,
            metrics_hash=record.metrics_hash,
            benchmark_hash=record.benchmark_hash,
            merkle_root=record.merkle_root,
            prev_hash=record.prev_hash,
            nonce=expected_nonce,
        )
        self._latest_nonce[record.org_id_hash] = expected_nonce
        self._tx_counter += 1

        return SubmitResult(
            tx_hash=f"0x{self._tx_counter:064x}",
            chain_id=0,  # local
            success=True,
            block_number=self._tx_counter,
        )

    async def batch_submit(
        self, records: Sequence[AttestationRecord]
    ) -> list[SubmitResult]:
        results = []
        for record in records:
            results.append(await self.submit(record))
        return results

    async def verify(
        self, org_id_hash: bytes, nonce: int
    ) -> AttestationRecord | None:
        return self._store.get(org_id_hash, {}).get(nonce)

    async def get_latest(self, org_id_hash: bytes) -> AttestationRecord | None:
        nonce = self._latest_nonce.get(org_id_hash, 0)
        if nonce == 0:
            return None
        return self._store[org_id_hash][nonce]

    async def get_latest_nonce(self, org_id_hash: bytes) -> int:
        return self._latest_nonce.get(org_id_hash, 0)
```

### Configuration

```python
# Additions to src/agentproof/config.py

class AgentProofConfig(BaseSettings):
    # ... existing fields ...

    # Attestation (Phase 2)
    attestation_enabled: bool = False
    attestation_provider: str = "local"           # "local", "evm"
    attestation_rpc_url: str | None = None
    attestation_contract_address: str | None = None
    attestation_private_key: str | None = None    # hot wallet key
    attestation_chain_id: int = 8453              # Base mainnet
    attestation_batch_size: int = 100
    attestation_schedule_cron: str = "0 2 * * *"  # 2:00 AM UTC daily
```

### Provider Factory

```python
from agentproof.config import get_config


def create_attestation_provider() -> AttestationProvider:
    """Construct the configured provider. No chain-specific imports leak upstream."""
    config = get_config()

    if config.attestation_provider == "local":
        from agentproof.attestation.local import LocalProvider
        return LocalProvider()

    if config.attestation_provider == "evm":
        from agentproof.attestation.evm import EVMProvider
        return EVMProvider(
            rpc_url=config.attestation_rpc_url,
            contract_address=config.attestation_contract_address,
            contract_abi=_load_contract_abi(),
            private_key=config.attestation_private_key,
            chain_id=config.attestation_chain_id,
        )

    raise ValueError(f"Unknown attestation provider: {config.attestation_provider}")
```

---

## 7. Cost Estimates

### Per-Attestation Bytes

| Field | Bytes |
|-------|-------|
| orgIdHash | 32 |
| periodStart (uint40) | 5 |
| periodEnd (uint40) | 5 |
| metricsHash | 32 |
| benchmarkHash | 32 |
| merkleRoot | 32 |
| prevHash | 32 |
| nonce (uint64) | 8 |
| **Total (on-chain storage)** | **178** |
| **Calldata (ABI-encoded)** | **~256** |

### Gas Cost Per Attestation (Base L2)

Assumptions: ETH = $3,200, Base median gas = 0.005 gwei, L1 blob fee negligible post-EIP-4844.

| Operation | Gas | USD |
|-----------|-----|-----|
| Single `attest` (cold SSTORE + SLOAD) | ~85,000 | $0.0014 |
| Single `attest` (warm, same block) | ~65,000 | $0.0010 |
| `batchAttest` per org (amortized) | ~55,000 | $0.0009 |

### Monthly Cost Projections

| Scenario | Orgs | Frequency | Method | Monthly Gas (USD) |
|----------|------|-----------|--------|-------------------|
| Pilot | 10 | Daily | Individual | $0.42 |
| Growth | 100 | Daily | Batch | $1.62 |
| Scale | 1,000 | Daily | Batch | $16.20 |
| Enterprise | 10,000 | Daily | Batch | $162.00 |

### Batch vs Individual Comparison

For 100 orgs, daily attestation:

| Method | Txs/day | Gas/day | USD/day | USD/month |
|--------|---------|---------|---------|-----------|
| Individual `attest` x 100 | 100 | 8,500,000 | $0.14 | $4.20 |
| `batchAttest` x 1 | 1 | 5,500,000 | $0.054 | $1.62 |
| **Savings from batching** | | | | **$2.58/mo (61%)** |

At 1,000 orgs the savings scale proportionally. Batching is always preferable unless an org needs sub-daily attestation cadence that cannot wait for the batch window.

### Cost of Off-Chain Storage

The off-chain storage cost is dominated by the `trace_evaluations` table. For an org with 10,000 traces/day, each row is ~120 bytes (UUID + attestation FK + trace_id + hash + index). That is 1.2 MB/day, 36 MB/month. Compression reduces this to ~12 MB/month. Negligible relative to the existing `llm_events` table.

---

## 8. Implementation Plan

### Directory Structure (New)

```
src/agentproof/
  attestation/
    __init__.py
    provider.py        # AttestationProvider ABC, AttestationRecord, SubmitResult
    local.py           # LocalProvider
    evm.py             # EVMProvider
    factory.py         # create_attestation_provider()
    merkle.py          # build_merkle_tree, generate_proof, verify_proof
    hasher.py          # compute_metrics_hash, compute_benchmark_hash, etc.
    service.py         # AttestationService (orchestrates the daily batch job)
    types.py           # Off-chain attestation Pydantic models

contracts/
  src/
    AgentProofAttestation.sol
  test/
    AgentProofAttestation.t.sol
  script/
    Deploy.s.sol
  foundry.toml
```

### Phase Breakdown

#### Phase 2A-Alpha (Weeks 1-3): Foundation

| Task | Description | Owner | Size |
|------|-------------|-------|------|
| 2A-alpha-1 | Set up Foundry project, write contract, unit tests | web3 | M (16-24h) |
| 2A-alpha-2 | Implement `AttestationProvider` ABC + `LocalProvider` | web3 | S (8-12h) |
| 2A-alpha-3 | Implement Merkle tree (build, prove, verify) with unit tests | web3 | M (12-16h) |
| 2A-alpha-4 | Implement hash functions (metrics, benchmark, attestation) | web3 | S (6-8h) |
| 2A-alpha-5 | DB migration: `attestations` + `trace_evaluations` tables | web3 | S (4-6h) |

**Deliverable:** `LocalProvider` passing all tests. Contract compiling and passing Foundry tests. Merkle tree verified against test vectors.

**Dependency:** None. Can start immediately.

#### Phase 2A-Beta (Weeks 4-6): Integration

| Task | Description | Owner | Size |
|------|-------------|-------|------|
| 2A-beta-1 | Implement `EVMProvider` with Base testnet (Sepolia) | web3 | M (16-20h) |
| 2A-beta-2 | Deploy contract to Base Sepolia, verify on Basescan | web3 | S (4-6h) |
| 2A-beta-3 | Implement `AttestationService` (daily batch orchestration) | web3 | L (20-28h) |
| 2A-beta-4 | Config additions, provider factory, env var wiring | web3 | S (4-6h) |
| 2A-beta-5 | Integration test: DB data -> Merkle tree -> testnet submission -> verification | web3 | M (12-16h) |

**Deliverable:** End-to-end attestation flow working on Base Sepolia. Attestation service can be triggered manually and produces confirmed on-chain records.

**Dependency:** Phase 1 must have produced `llm_events` and `benchmark_results` data (even synthetic) for the attestation service to consume.

#### Phase 2A-Gamma (Weeks 7-10): Hardening and Audit

| Task | Description | Owner | Size |
|------|-------------|-------|------|
| 2A-gamma-1 | Gas optimization: benchmark batch sizes, tune calldata encoding | web3 | S (8-10h) |
| 2A-gamma-2 | Verification API endpoints (prove a trace, prove a period) | web3 | M (12-16h) |
| 2A-gamma-3 | Contract audit preparation: documentation, invariant tests, fuzzing | web3 | M (16-20h) |
| 2A-gamma-4 | External contract audit | external | XL (2-3 weeks elapsed) |
| 2A-gamma-5 | Base mainnet deployment (post-audit) | web3 | S (4-6h) |
| 2A-gamma-6 | Monitoring: tx failure alerts, nonce gap detection, gas price tracking | web3 | S (8-10h) |

**Deliverable:** Audited contract deployed to Base mainnet. Attestation service running on a daily cron. Verification endpoints available in the API.

**Dependency:** 2A-gamma-4 (audit) is an external dependency with 2-3 week lead time. Submit for audit at the end of Week 7 to avoid blocking mainnet deployment.

### What the Web3 Engineer Builds First

1. **LocalProvider + contract interface** (Week 1). This unblocks all downstream integration work. Other engineers can code against `AttestationProvider` immediately without needing a testnet.
2. **Merkle tree library** (Week 1-2). This is a pure computation module with no external dependencies. Extensive test coverage here prevents subtle verification failures later.
3. **Contract + Foundry tests** (Week 2-3). The contract is the riskiest component (immutable once deployed). Thorough testing before any testnet deployment.

### What Needs External Review

- **Contract audit.** The contract is ~200 lines of Solidity with straightforward storage patterns, but it handles financial attestations. An audit is required before mainnet deployment. Budget: $15k-$25k. Timeline: 2-3 weeks from submission.
- **Merkle tree implementation review.** The off-chain Merkle code must produce identical results to any on-chain verification. A second engineer should independently implement `verify_proof` and compare outputs against the primary implementation.

### Timeline Relative to Phase 1

```
Phase 1:  |===== Weeks 1-10 =====|
Phase 2A: |                 |===== Weeks 1-12 (2A) =====|
                            ^
                            |
                     Phase 1 interfaces frozen (ADR-003 Section 6)
                     2A design starts here (can overlap with Phase 1 Weeks 7-10)

Week 1-3  (2A-Alpha): Foundation -- no Phase 1 dependency
Week 4-6  (2A-Beta):  Integration -- needs Phase 1 data (even synthetic)
Week 7-10 (2A-Gamma): Hardening -- needs Phase 1 complete for real data
Week 8-10            : Audit window (overlaps with gamma)
Week 11-12           : Mainnet deployment + monitoring
```

---

## 9. Risks

### Performance Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Merkle tree construction is slow for orgs with 100k+ traces/day | Low | Medium | Tree construction is O(n log n). For 100k leaves, this is ~1.7M hash operations. SHA-256 throughput is ~500 MB/s on modern hardware. 100k leaves at 64 bytes each = 6.4 MB total hash input. Completion in <1 second. Profile during beta and add parallelism if needed. |
| L2 gas spikes during network congestion | Medium | Low | Daily batch job includes a gas price ceiling. If gas exceeds 10x median, defer to the next hour. Attestations are not latency-sensitive; a few hours of delay is acceptable. |
| Batch transaction exceeds block gas limit | Low | Medium | Base block gas limit is 60M. Batch of 100 attestations uses ~5.5M gas. Safe margin of 10x. If org count exceeds 1,000, split into multiple batches of 500. |

### Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Hot wallet private key compromise | Low | High | Hot wallet holds minimal ETH (1-2 days of gas). No withdrawal function in the contract. Attacker can only submit garbage attestations, which are detectable by nonce gaps. Implement key rotation: `setAttestor` revokes old key, authorizes new one. |
| Nonce desync between off-chain DB and on-chain state | Medium | Medium | Always read `latestNonce` from the contract before submitting. The off-chain DB is the secondary record; the contract is authoritative. Add a reconciliation job that compares DB nonce with on-chain nonce daily. |
| RPC provider outage | Medium | Low | Configure two RPC providers (Alchemy primary, QuickNode fallback). The provider factory accepts a list of RPC URLs and failovers. |

### Business Intelligence Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Hash algorithm change breaks historical verification | Low | High | SHA-256 is the canonical algorithm, locked in ADR-003 Section 6. Any algorithm change requires a version field in the attestation and dual-hash support for a migration period. |
| Fitness matrix schema change invalidates benchmark hashes | Medium | Medium | The `benchmarkHash` commits to a specific snapshot structure. If `FitnessEntry` fields change, the hash structure must be versioned. Add a `schema_version` field to the off-chain attestation record. |

### Security Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Attestation of fabricated data | Medium | High | The attestation system trusts the off-chain data. If the DB is compromised, the attestor can commit false hashes. Mitigation: the chain linkage makes retroactive tampering detectable (changing one attestation breaks the hash chain). Future work (Phase 3): multi-party attestation where the org co-signs. |
| Front-running of attestation transactions | Low | Low | Attestations are not order-dependent or value-bearing. Front-running has no economic incentive. |
| Contract upgrade risk (immutable) | N/A | N/A | The contract is intentionally non-upgradeable. If a bug is found, deploy a new contract and migrate by re-attesting from the last known good state. The `prevHash` chain breaks at the migration point, which is acceptable and documented. |

---

## 10. Unknowns

| # | Unknown | Impact | Investigation |
|---|---------|--------|---------------|
| U1 | **EIP-4844 blob pricing stability.** Post-Dencun, L1 data costs dropped dramatically, but blob prices could rise if demand increases. How sensitive is our cost model to 10x blob price increase? | Medium | Run the cost model at 1x, 5x, and 10x current blob prices. At 10x, batch of 100 still costs <$1/day. Acceptable. Monitor blob base fee weekly. |
| U2 | **Cross-chain verification demand.** Will auditors want to verify attestations from a different chain than where they were submitted? | Low (Phase 2), Medium (Phase 3) | The chain-agnostic abstraction supports this, but cross-chain proof relay is Phase 3 scope. Document the limitation. |
| U3 | **Merkle tree leaf ordering stability.** If traces are ingested out of order (late-arriving events), does the Merkle root change? | High | Yes. The attestation period must be closed (no new events accepted) before tree construction. Enforce a grace period: attest for day N on day N+1 after 2:00 AM UTC. Late events arriving after the grace period are included in the next attestation. |
| U4 | **Contract size limit.** The `getByPeriod` function does unbounded iteration. Will the compiled bytecode exceed the 24KB Spurious Dragon limit? | Low | Estimate: the contract compiles to ~8KB. Well within limits. Verify during 2A-alpha-1. |
| U5 | **Multi-chain attestation for a single org.** Can an org have attestations on multiple chains? | Medium | Not in Phase 2. The `attestation_chain_id` config is global. Per-org chain selection is Phase 3. The schema supports it (chain_id is stored in the off-chain record). |
| U6 | **Exact gas costs on Base mainnet vs testnet.** Testnet gas dynamics differ from mainnet. Are our cost projections accurate? | Low | Deploy to mainnet early (with a test org) and measure actual costs before committing to customer-facing pricing. |

---

## Appendix A: File Locations

All paths are absolute, relative to the repository root at `/Users/hju/Documents/BlockWorks`.

| Component | Path |
|-----------|------|
| Existing content hasher | `/Users/hju/Documents/BlockWorks/src/agentproof/pipeline/hasher.py` |
| Existing core types | `/Users/hju/Documents/BlockWorks/src/agentproof/types.py` |
| Existing benchmarking types | `/Users/hju/Documents/BlockWorks/src/agentproof/benchmarking/types.py` |
| Existing waste types | `/Users/hju/Documents/BlockWorks/src/agentproof/waste/types.py` |
| Existing API schemas | `/Users/hju/Documents/BlockWorks/src/agentproof/api/schemas.py` |
| Existing config | `/Users/hju/Documents/BlockWorks/src/agentproof/config.py` |
| New: attestation provider ABC | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/provider.py` |
| New: local provider | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/local.py` |
| New: EVM provider | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/evm.py` |
| New: provider factory | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/factory.py` |
| New: Merkle tree library | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/merkle.py` |
| New: attestation hash functions | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/hasher.py` |
| New: attestation service | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/service.py` |
| New: attestation Pydantic models | `/Users/hju/Documents/BlockWorks/src/agentproof/attestation/types.py` |
| New: Solidity contract | `/Users/hju/Documents/BlockWorks/contracts/src/AgentProofAttestation.sol` |
| New: Foundry tests | `/Users/hju/Documents/BlockWorks/contracts/test/AgentProofAttestation.t.sol` |
| New: deploy script | `/Users/hju/Documents/BlockWorks/contracts/script/Deploy.s.sol` |
| New: Foundry config | `/Users/hju/Documents/BlockWorks/contracts/foundry.toml` |

## Appendix B: Hash Algorithm Concordance

The system uses two hash algorithms at different layers. Mixing them up breaks verification.

| Layer | Algorithm | Reason |
|-------|-----------|--------|
| Off-chain content hashing (metrics, benchmarks, traces) | SHA-256 | Established in Phase 0 (`hash_content`). Deterministic, widely supported, no EVM dependency. |
| On-chain chain linkage (`_computeAttestationHash`) | Keccak-256 | Native EVM opcode. Using SHA-256 on-chain costs ~10x more gas (no native opcode). |
| Org ID pseudonymization (`orgIdHash` on-chain) | Keccak-256 | Computed on-chain for verification. Must use the cheap opcode. |
| Org ID pseudonymization (off-chain storage) | SHA-256 | Stored in Postgres for indexing. The on-chain value is keccak, the off-chain value is SHA-256. They serve different purposes: off-chain is for DB lookup, on-chain is for contract lookup. |

The off-chain attestation service must compute BOTH hashes when preparing a submission: SHA-256 for content hashes (metrics, benchmark, Merkle root) and Keccak-256 for `orgIdHash` and `prevHash` (chain linkage). The `EVMProvider` handles Keccak computation using `web3.solidity_keccak`.
