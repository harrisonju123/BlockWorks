"""In-memory attestation provider for development and testing.

Replicates the on-chain contract's chain-linkage and nonce enforcement
so that all Phase 2 code can develop against a fast, deterministic
provider without needing a testnet or L2 node.

Invariants enforced (matching the Solidity contract):
  - Nonces are sequential: each org's next nonce must be prev + 1.
  - Chain linkage: record.prev_hash must equal the SHA-256 hash of the
    serialized previous AttestationRecord (empty string for nonce 0).
  - No duplicate (org_id_hash, nonce) pairs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from agentproof.attestation.hashing import compute_chain_hash
from agentproof.attestation.provider import AttestationError, AttestationProvider
from agentproof.attestation.types import AttestationRecord

# Sentinel for first attestation — matches Solidity's bytes32(0)
ZERO_HASH = "0" * 64


class LocalProvider(AttestationProvider):
    """In-memory attestation provider for development and testing.

    Stores attestations in a dict keyed by (org_id_hash, nonce).
    Enforces chain linkage and sequential nonces to mirror what the
    Solidity contract will do on-chain.
    """

    def __init__(self) -> None:
        # org_id_hash -> nonce -> AttestationRecord
        self._store: dict[str, dict[int, AttestationRecord]] = defaultdict(dict)
        self._latest_nonce: dict[str, int] = defaultdict(int)
        self._tx_counter = 0

    async def submit(self, record: AttestationRecord) -> str:
        if not record.org_id_hash:
            raise AttestationError("org_id_hash must not be empty")

        expected_nonce = self._latest_nonce[record.org_id_hash] + 1

        if record.nonce != expected_nonce:
            raise AttestationError(
                f"Nonce mismatch for org {record.org_id_hash}: "
                f"expected {expected_nonce}, got {record.nonce}"
            )

        # Duplicate nonce guard (shouldn't happen if nonce check passes,
        # but belt-and-suspenders against concurrent misuse)
        if record.nonce in self._store[record.org_id_hash]:
            raise AttestationError(
                f"Duplicate nonce {record.nonce} for org {record.org_id_hash}"
            )

        # Chain linkage: prev_hash must match the hash of the previous record
        self._validate_chain_linkage(record)

        self._store[record.org_id_hash][record.nonce] = record
        self._latest_nonce[record.org_id_hash] = record.nonce
        self._tx_counter += 1

        return f"local-tx-{self._tx_counter:08d}"

    async def batch_submit(self, records: list[AttestationRecord]) -> list[str]:
        """Submit multiple attestations in order within a single 'transaction'.

        Rolls back all changes if any individual submission fails, matching
        the all-or-nothing semantics of a Solidity batchAttest call.
        """
        # Snapshot state so we can roll back on failure
        store_snapshot = {
            org: dict(nonces) for org, nonces in self._store.items()
        }
        nonce_snapshot = dict(self._latest_nonce)
        tx_snapshot = self._tx_counter

        tx_ids: list[str] = []
        try:
            for record in records:
                tx_id = await self.submit(record)
                tx_ids.append(tx_id)
        except AttestationError:
            # Roll back: restore all state to pre-batch
            self._store = defaultdict(dict, store_snapshot)
            self._latest_nonce = defaultdict(int, nonce_snapshot)
            self._tx_counter = tx_snapshot
            raise

        return tx_ids

    async def verify(
        self,
        org_id_hash: str,
        period_start: datetime,
        period_end: datetime,
    ) -> AttestationRecord | None:
        """Find an attestation matching the org and time period.

        Iterates backward from the latest nonce to find a record whose
        period_start and period_end match the query. Returns None if no
        matching attestation exists.
        """
        org_records = self._store.get(org_id_hash)
        if not org_records:
            return None

        latest = self._latest_nonce.get(org_id_hash, 0)
        for nonce in range(latest, 0, -1):
            record = org_records.get(nonce)
            if record is None:
                continue
            if record.period_start == period_start and record.period_end == period_end:
                return record
        return None

    async def get_latest(self, org_id_hash: str) -> AttestationRecord | None:
        nonce = self._latest_nonce.get(org_id_hash, 0)
        if nonce == 0:
            return None
        return self._store[org_id_hash][nonce]

    async def get_latest_nonce(self, org_id_hash: str) -> int:
        return self._latest_nonce.get(org_id_hash, 0)

    def _validate_chain_linkage(self, record: AttestationRecord) -> None:
        """Ensure prev_hash matches the hash of the previous attestation.

        For nonce 1 (first attestation), prev_hash must be the empty string.
        For subsequent nonces, it must be the SHA-256 of the previous record.
        """
        if record.nonce == 1:
            if record.prev_hash not in ("", ZERO_HASH):
                raise AttestationError(
                    f"First attestation (nonce=1) for org {record.org_id_hash} "
                    f"must have zero prev_hash, got '{record.prev_hash}'"
                )
            return

        prev_record = self._store[record.org_id_hash].get(record.nonce - 1)
        if prev_record is None:
            raise AttestationError(
                f"Previous record (nonce={record.nonce - 1}) not found "
                f"for org {record.org_id_hash}"
            )

        expected_prev_hash = compute_chain_hash(prev_record)
        if record.prev_hash != expected_prev_hash:
            raise AttestationError(
                f"Chain linkage broken for org {record.org_id_hash} nonce {record.nonce}: "
                f"expected prev_hash '{expected_prev_hash}', got '{record.prev_hash}'"
            )
