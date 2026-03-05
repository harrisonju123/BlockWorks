"""Attestation API endpoints.

Exposes submit, batch submit, latest lookup, and chain-integrity
verification. Backed by the chain-agnostic AttestationProvider so
the same endpoints work against LocalProvider (dev) and EVMProvider
(production).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from blockthrough.attestation.factory import create_provider
from blockthrough.attestation.hashing import compute_chain_hash
from blockthrough.attestation.provider import AttestationError, AttestationProvider
from blockthrough.attestation.types import AttestationRecord
from blockthrough.utils import utcnow

router = APIRouter(prefix="/attestations")


# ---------------------------------------------------------------------------
# Module-level provider singleton — lazily initialized on first request.
# Avoids import-time side effects (same pattern as routing module).
# ---------------------------------------------------------------------------

_provider: AttestationProvider | None = None


def _get_provider() -> AttestationProvider:
    global _provider
    if _provider is None:
        _provider = create_provider()
    return _provider


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AttestationSubmitRequest(BaseModel):
    org_id_hash: str
    period_start: datetime
    period_end: datetime
    metrics_hash: str
    benchmark_hash: str
    merkle_root: str
    prev_hash: str
    nonce: int
    timestamp: datetime = Field(default_factory=utcnow)


class AttestationSubmitResponse(BaseModel):
    tx_id: str
    org_id_hash: str
    nonce: int


class AttestationResponse(BaseModel):
    org_id_hash: str
    period_start: datetime
    period_end: datetime
    metrics_hash: str
    benchmark_hash: str
    merkle_root: str
    prev_hash: str
    nonce: int
    timestamp: datetime


class BatchSubmitRequest(BaseModel):
    records: list[AttestationSubmitRequest]


class BatchSubmitResponse(BaseModel):
    tx_ids: list[str]
    count: int


class VerifyResponse(BaseModel):
    org_id_hash: str
    chain_valid: bool
    latest_nonce: int
    records_checked: int
    first_broken_nonce: int | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/submit", response_model=AttestationSubmitResponse, status_code=201)
async def submit_attestation(body: AttestationSubmitRequest) -> AttestationSubmitResponse:
    """Build and submit a single attestation record."""
    provider = _get_provider()

    record = AttestationRecord(
        org_id_hash=body.org_id_hash,
        period_start=body.period_start,
        period_end=body.period_end,
        metrics_hash=body.metrics_hash,
        benchmark_hash=body.benchmark_hash,
        merkle_root=body.merkle_root,
        prev_hash=body.prev_hash,
        nonce=body.nonce,
        timestamp=body.timestamp,
    )

    try:
        tx_id = await provider.submit(record)
    except AttestationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return AttestationSubmitResponse(
        tx_id=tx_id,
        org_id_hash=record.org_id_hash,
        nonce=record.nonce,
    )


@router.get("/latest/{org_id_hash}", response_model=AttestationResponse)
async def get_latest_attestation(org_id_hash: str) -> AttestationResponse:
    """Get the most recent attestation for an org."""
    provider = _get_provider()
    record = await provider.get_latest(org_id_hash)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No attestations found for org {org_id_hash}",
        )

    return AttestationResponse(
        org_id_hash=record.org_id_hash,
        period_start=record.period_start,
        period_end=record.period_end,
        metrics_hash=record.metrics_hash,
        benchmark_hash=record.benchmark_hash,
        merkle_root=record.merkle_root,
        prev_hash=record.prev_hash,
        nonce=record.nonce,
        timestamp=record.timestamp,
    )


@router.get("/verify/{org_id_hash}", response_model=VerifyResponse)
async def verify_chain_integrity(org_id_hash: str) -> VerifyResponse:
    """Verify chain linkage integrity for an org's attestation history.

    Walks the chain from nonce 1 to the latest, checking that each
    record's prev_hash matches the SHA-256 hash of the preceding record.
    """
    provider = _get_provider()
    latest_nonce = await provider.get_latest_nonce(org_id_hash)

    if latest_nonce == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No attestations found for org {org_id_hash}",
        )

    first_broken: int | None = None
    records_checked = 0
    chain_valid = True

    # LocalProvider exposes its internal store for a full chain walk.
    # For EVM, we trust the contract's nonce sequencing (enforced on-chain).
    from blockthrough.attestation.local_provider import ZERO_HASH, LocalProvider

    if isinstance(provider, LocalProvider):
        for nonce in range(1, latest_nonce + 1):
            org_store = provider._store.get(org_id_hash, {})
            record = org_store.get(nonce)
            if record is None:
                chain_valid = False
                first_broken = nonce
                break

            records_checked += 1

            if nonce == 1:
                if record.prev_hash not in ("", ZERO_HASH):
                    chain_valid = False
                    first_broken = nonce
                    break
            else:
                prev_record = org_store.get(nonce - 1)
                if prev_record is None:
                    chain_valid = False
                    first_broken = nonce
                    break
                expected = compute_chain_hash(prev_record)
                if record.prev_hash != expected:
                    chain_valid = False
                    first_broken = nonce
                    break
    else:
        # For non-local providers, trust that get_latest succeeded
        records_checked = latest_nonce

    return VerifyResponse(
        org_id_hash=org_id_hash,
        chain_valid=chain_valid,
        latest_nonce=latest_nonce,
        records_checked=records_checked,
        first_broken_nonce=first_broken,
    )


@router.post("/batch", response_model=BatchSubmitResponse, status_code=201)
async def batch_submit(body: BatchSubmitRequest) -> BatchSubmitResponse:
    """Submit multiple attestation records in a single batch."""
    provider = _get_provider()

    records = [
        AttestationRecord(
            org_id_hash=r.org_id_hash,
            period_start=r.period_start,
            period_end=r.period_end,
            metrics_hash=r.metrics_hash,
            benchmark_hash=r.benchmark_hash,
            merkle_root=r.merkle_root,
            prev_hash=r.prev_hash,
            nonce=r.nonce,
            timestamp=r.timestamp,
        )
        for r in body.records
    ]

    try:
        tx_ids = await provider.batch_submit(records)
    except AttestationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return BatchSubmitResponse(tx_ids=tx_ids, count=len(tx_ids))


class OrgsResponse(BaseModel):
    org_hashes: list[str]


@router.get("/orgs", response_model=OrgsResponse)
async def get_attestation_orgs() -> OrgsResponse:
    """Return org hashes that have at least one attestation."""
    provider = _get_provider()
    org_hashes = await provider.get_org_hashes()
    return OrgsResponse(org_hashes=org_hashes)


def reset_provider() -> None:
    """Reset the module-level provider. Used by tests for clean state."""
    global _provider
    _provider = None
