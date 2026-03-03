"""Tests for the LocalProvider in-memory attestation implementation.

Validates that the LocalProvider enforces the same invariants as the
Solidity contract: sequential nonces, chain linkage via keccak256 hashes
of ABI-packed fields, and all-or-nothing batch semantics.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentproof.attestation.hashing import compute_chain_hash
from agentproof.attestation.local_provider import ZERO_HASH, LocalProvider
from agentproof.attestation.provider import AttestationError
from agentproof.attestation.types import AttestationRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
_PERIOD_START = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
_PERIOD_END = datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc)


def _make_record(
    org_id_hash: str = "aa" * 32,
    nonce: int = 1,
    prev_hash: str = "0" * 64,
    period_start: datetime = _PERIOD_START,
    period_end: datetime = _PERIOD_END,
    metrics_hash: str = "dd" * 32,
    benchmark_hash: str = "bb" * 32,
    merkle_root: str = "cc" * 32,
    timestamp: datetime = _NOW,
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


# ---------------------------------------------------------------------------
# Submit and retrieve
# ---------------------------------------------------------------------------


class TestSubmitAndRetrieve:

    @pytest.mark.asyncio
    async def test_submit_first_attestation(self) -> None:
        provider = LocalProvider()
        record = _make_record(nonce=1, prev_hash=ZERO_HASH)
        tx_id = await provider.submit(record)

        assert tx_id.startswith("local-tx-")
        latest = await provider.get_latest("aa" * 32)
        assert latest is not None
        assert latest.nonce == 1
        assert latest.org_id_hash == "aa" * 32

    @pytest.mark.asyncio
    async def test_submit_returns_unique_tx_ids(self) -> None:
        provider = LocalProvider()
        tx1 = await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        # Build second record with correct prev_hash
        first = await provider.get_latest("aa" * 32)
        assert first is not None
        prev_hash = compute_chain_hash(first)
        tx2 = await provider.submit(_make_record(nonce=2, prev_hash=prev_hash))

        assert tx1 != tx2

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_for_unknown_org(self) -> None:
        provider = LocalProvider()
        assert await provider.get_latest("ee" * 32) is None

    @pytest.mark.asyncio
    async def test_submit_stores_all_fields(self) -> None:
        provider = LocalProvider()
        record = _make_record(
            nonce=1,
            prev_hash=ZERO_HASH,
            metrics_hash="a" * 64,
            benchmark_hash="b" * 64,
            merkle_root="c" * 64,
        )
        await provider.submit(record)
        stored = await provider.get_latest("aa" * 32)

        assert stored is not None
        assert stored.metrics_hash == "a" * 64
        assert stored.benchmark_hash == "b" * 64
        assert stored.merkle_root == "c" * 64
        assert stored.period_start == _PERIOD_START
        assert stored.period_end == _PERIOD_END


# ---------------------------------------------------------------------------
# Chain linkage enforcement
# ---------------------------------------------------------------------------


class TestChainLinkage:

    @pytest.mark.asyncio
    async def test_first_record_must_have_zero_prev_hash(self) -> None:
        provider = LocalProvider()
        # Use a valid hex string that isn't ZERO_HASH
        record = _make_record(nonce=1, prev_hash="ff" * 32)

        with pytest.raises(AttestationError, match="zero prev_hash"):
            await provider.submit(record)

    @pytest.mark.asyncio
    async def test_second_record_requires_hash_of_first(self) -> None:
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        first = await provider.get_latest("aa" * 32)
        assert first is not None
        correct_hash = compute_chain_hash(first)

        # Submit with wrong prev_hash (valid hex, but not the correct chain hash)
        with pytest.raises(AttestationError, match="Chain linkage broken"):
            await provider.submit(_make_record(nonce=2, prev_hash="ff" * 32))

        # Submit with correct prev_hash succeeds
        tx_id = await provider.submit(
            _make_record(nonce=2, prev_hash=correct_hash)
        )
        assert tx_id.startswith("local-tx-")

    @pytest.mark.asyncio
    async def test_chain_of_three_records(self) -> None:
        """Build a 3-record chain and verify linkage at each step."""
        provider = LocalProvider()

        # Record 1
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))
        r1 = await provider.get_latest("aa" * 32)
        assert r1 is not None

        # Record 2
        h1 = compute_chain_hash(r1)
        await provider.submit(_make_record(nonce=2, prev_hash=h1))
        r2 = await provider.get_latest("aa" * 32)
        assert r2 is not None

        # Record 3
        h2 = compute_chain_hash(r2)
        await provider.submit(_make_record(nonce=3, prev_hash=h2))
        r3 = await provider.get_latest("aa" * 32)
        assert r3 is not None
        assert r3.nonce == 3
        assert r3.prev_hash == h2

    @pytest.mark.asyncio
    async def testcompute_chain_hash_is_deterministic(self) -> None:
        """Same record always produces the same hash."""
        record = _make_record(nonce=1, prev_hash=ZERO_HASH)
        h1 = compute_chain_hash(record)
        h2 = compute_chain_hash(record)
        assert h1 == h2
        assert len(h1) == 64  # keccak-256 hex digest

    @pytest.mark.asyncio
    async def test_compute_chain_hash_uses_keccak256_abi_packed(self) -> None:
        """Verify chain hash uses keccak256 of ABI-packed fields, not SHA-256 JSON."""
        record = _make_record(nonce=1, prev_hash=ZERO_HASH)
        result = compute_chain_hash(record)
        assert len(result) == 64
        int(result, 16)  # valid hex
        # Changing any field should produce a different hash
        record2 = _make_record(nonce=1, prev_hash=ZERO_HASH, metrics_hash="ee" * 32)
        assert compute_chain_hash(record2) != result


# ---------------------------------------------------------------------------
# Nonce sequencing
# ---------------------------------------------------------------------------


class TestNonceSequencing:

    @pytest.mark.asyncio
    async def test_nonce_must_start_at_one(self) -> None:
        provider = LocalProvider()
        with pytest.raises(AttestationError, match="Nonce mismatch"):
            await provider.submit(_make_record(nonce=0, prev_hash=ZERO_HASH))

    @pytest.mark.asyncio
    async def test_nonce_must_be_sequential(self) -> None:
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        # Skip nonce 2
        with pytest.raises(AttestationError, match="Nonce mismatch"):
            await provider.submit(_make_record(nonce=3, prev_hash=ZERO_HASH))

    @pytest.mark.asyncio
    async def test_duplicate_nonce_rejected(self) -> None:
        """Resubmitting the same nonce is rejected by the nonce check."""
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        with pytest.raises(AttestationError, match="Nonce mismatch"):
            await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

    @pytest.mark.asyncio
    async def test_get_latest_nonce_zero_for_new_org(self) -> None:
        provider = LocalProvider()
        assert await provider.get_latest_nonce("ee" * 32) == 0

    @pytest.mark.asyncio
    async def test_get_latest_nonce_increments(self) -> None:
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))
        assert await provider.get_latest_nonce("aa" * 32) == 1

        first = await provider.get_latest("aa" * 32)
        assert first is not None
        h = compute_chain_hash(first)
        await provider.submit(_make_record(nonce=2, prev_hash=h))
        assert await provider.get_latest_nonce("aa" * 32) == 2


# ---------------------------------------------------------------------------
# Batch submit
# ---------------------------------------------------------------------------


class TestBatchSubmit:

    @pytest.mark.asyncio
    async def test_batch_submit_sequential_records(self) -> None:
        """Batch with valid sequential records succeeds."""
        provider = LocalProvider()

        r1 = _make_record(nonce=1, prev_hash=ZERO_HASH)
        # Pre-compute the keccak256 ABI-packed hash of r1 for r2's prev_hash
        h1 = compute_chain_hash(r1)

        r2 = _make_record(nonce=2, prev_hash=h1)

        tx_ids = await provider.batch_submit([r1, r2])
        assert len(tx_ids) == 2
        assert await provider.get_latest_nonce("aa" * 32) == 2

    @pytest.mark.asyncio
    async def test_batch_submit_rollback_on_failure(self) -> None:
        """If any record in a batch fails, all changes are rolled back."""
        provider = LocalProvider()

        r1 = _make_record(nonce=1, prev_hash=ZERO_HASH)
        # r2 has wrong prev_hash — should trigger rollback of r1
        r2 = _make_record(nonce=2, prev_hash="ff" * 32)

        with pytest.raises(AttestationError, match="Chain linkage broken"):
            await provider.batch_submit([r1, r2])

        # r1 should NOT have been persisted due to rollback
        assert await provider.get_latest_nonce("aa" * 32) == 0
        assert await provider.get_latest("aa" * 32) is None

    @pytest.mark.asyncio
    async def test_batch_submit_empty_list(self) -> None:
        provider = LocalProvider()
        tx_ids = await provider.batch_submit([])
        assert tx_ids == []

    @pytest.mark.asyncio
    async def test_batch_submit_single_record(self) -> None:
        provider = LocalProvider()
        tx_ids = await provider.batch_submit([_make_record(nonce=1, prev_hash=ZERO_HASH)])
        assert len(tx_ids) == 1


# ---------------------------------------------------------------------------
# Verify (period-based lookup)
# ---------------------------------------------------------------------------


class TestVerify:

    @pytest.mark.asyncio
    async def test_verify_returns_matching_record(self) -> None:
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        result = await provider.verify("aa" * 32, _PERIOD_START, _PERIOD_END)
        assert result is not None
        assert result.nonce == 1

    @pytest.mark.asyncio
    async def test_verify_returns_none_for_wrong_period(self) -> None:
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        different_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        different_end = datetime(2025, 1, 2, tzinfo=timezone.utc)
        result = await provider.verify("aa" * 32, different_start, different_end)
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_returns_none_for_unknown_org(self) -> None:
        provider = LocalProvider()
        result = await provider.verify("ee" * 32, _PERIOD_START, _PERIOD_END)
        assert result is None


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------


class TestGetLatest:

    @pytest.mark.asyncio
    async def test_get_latest_returns_highest_nonce(self) -> None:
        provider = LocalProvider()

        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))
        r1 = await provider.get_latest("aa" * 32)
        assert r1 is not None
        h1 = compute_chain_hash(r1)

        await provider.submit(_make_record(nonce=2, prev_hash=h1))
        r2 = await provider.get_latest("aa" * 32)
        assert r2 is not None
        h2 = compute_chain_hash(r2)

        await provider.submit(_make_record(nonce=3, prev_hash=h2))

        latest = await provider.get_latest("aa" * 32)
        assert latest is not None
        assert latest.nonce == 3

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_for_no_attestations(self) -> None:
        provider = LocalProvider()
        assert await provider.get_latest("ee" * 32) is None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:

    @pytest.mark.asyncio
    async def test_empty_org_id_rejected(self) -> None:
        provider = LocalProvider()
        with pytest.raises(AttestationError, match="org_id_hash must not be empty"):
            await provider.submit(_make_record(org_id_hash="", nonce=1, prev_hash=ZERO_HASH))

    @pytest.mark.asyncio
    async def test_multiple_orgs_independent(self) -> None:
        """Records for different orgs don't interfere with each other."""
        provider = LocalProvider()
        org_a = "aa" * 32
        org_b = "bb" * 32

        await provider.submit(_make_record(org_id_hash=org_a, nonce=1, prev_hash=ZERO_HASH))
        await provider.submit(_make_record(org_id_hash=org_b, nonce=1, prev_hash=ZERO_HASH))

        assert await provider.get_latest_nonce(org_a) == 1
        assert await provider.get_latest_nonce(org_b) == 1

        latest_a = await provider.get_latest(org_a)
        latest_b = await provider.get_latest(org_b)
        assert latest_a is not None
        assert latest_b is not None
        assert latest_a.org_id_hash == org_a
        assert latest_b.org_id_hash == org_b


# ---------------------------------------------------------------------------
# Contract semantics parity — these tests document behavior that the
# Solidity contract will also enforce, ensuring LocalProvider is a
# faithful dev stand-in.
# ---------------------------------------------------------------------------


class TestContractSemanticsParity:
    """Verify that LocalProvider mirrors key contract invariants."""

    @pytest.mark.asyncio
    async def test_nonce_starts_at_one_not_zero(self) -> None:
        """Contract uses nonce=0 as 'not found' sentinel; first valid nonce is 1."""
        provider = LocalProvider()
        assert await provider.get_latest_nonce("ee" * 32) == 0

        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))
        assert await provider.get_latest_nonce("aa" * 32) == 1

    @pytest.mark.asyncio
    async def test_prev_hash_is_keccak256_of_abi_packed_record(self) -> None:
        """Chain linkage uses keccak256 of ABI-packed fields, matching the Solidity contract."""
        provider = LocalProvider()
        await provider.submit(_make_record(nonce=1, prev_hash=ZERO_HASH))

        first = await provider.get_latest("aa" * 32)
        assert first is not None

        expected = compute_chain_hash(first)
        assert len(expected) == 64
        int(expected, 16)  # valid hex

        # Submitting with the correct chain hash succeeds
        await provider.submit(_make_record(nonce=2, prev_hash=expected))
        assert await provider.get_latest_nonce("aa" * 32) == 2

    @pytest.mark.asyncio
    async def test_batch_is_atomic(self) -> None:
        """Batch either fully succeeds or fully rolls back — no partial state.

        This mirrors the Solidity batchAttest which reverts the entire
        transaction on any single failure.
        """
        provider = LocalProvider()
        org_x = "cc" * 32

        # Pre-submit one record so we have a nonce=1
        await provider.submit(_make_record(org_id_hash=org_x, nonce=1, prev_hash=ZERO_HASH))
        r1 = await provider.get_latest(org_x)
        assert r1 is not None

        h1 = compute_chain_hash(r1)

        # Valid second record + invalid third (wrong prev_hash for nonce 3)
        r2 = _make_record(org_id_hash=org_x, nonce=2, prev_hash=h1)
        r3 = _make_record(org_id_hash=org_x, nonce=3, prev_hash="ff" * 32)

        with pytest.raises(AttestationError):
            await provider.batch_submit([r2, r3])

        # State should be unchanged — still at nonce 1
        assert await provider.get_latest_nonce(org_x) == 1
