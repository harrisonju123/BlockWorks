"""Billing attestation — hashes reconciliation data and submits on-chain.

Produces a BillingAttestation that cryptographically commits to:
- What we independently observed (token counts, cost)
- What the provider billed
- The computed discrepancy

The attestation hash is then submitted to the attestation provider
for on-chain anchoring, giving orgs a verifiable record that their
AI spend was independently audited.
"""

from __future__ import annotations

from datetime import datetime

from blockthrough.attestation.provider import AttestationProvider
from blockthrough.billing.types import BillingAttestation, ReconciliationResult
from blockthrough.pipeline.hasher import hash_content


def _hash_reconciliations(reconciliations: list[ReconciliationResult]) -> str:
    """Canonical hash of the full reconciliation dataset.

    Sorted by (provider, model) to ensure determinism regardless of
    input ordering. Floats rounded to 6 decimals to prevent IEEE 754 drift.
    """
    sorted_recs = sorted(
        reconciliations,
        key=lambda r: (r.provider, r.model or ""),
    )

    payload = [
        {
            "provider": r.provider,
            "model": r.model,
            "observed_prompt_tokens": r.observed_prompt_tokens,
            "observed_completion_tokens": r.observed_completion_tokens,
            "observed_cost": round(r.observed_cost, 6),
            "billed_prompt_tokens": r.billed_prompt_tokens,
            "billed_completion_tokens": r.billed_completion_tokens,
            "billed_cost": round(r.billed_cost, 6),
            "discrepancy_total_tokens": r.discrepancy_total_tokens,
            "discrepancy_cost": round(r.discrepancy_cost, 6),
        }
        for r in sorted_recs
    ]
    return hash_content(payload)


def create_billing_attestation(
    reconciliations: list[ReconciliationResult],
    org_id: str,
    period_start: datetime,
    period_end: datetime,
) -> BillingAttestation:
    """Build a billing attestation from reconciliation results.

    Computes aggregate totals and a deterministic hash of the full
    reconciliation dataset. The hash can be submitted to the attestation
    provider for on-chain anchoring.
    """
    total_observed = sum(r.observed_cost for r in reconciliations)
    total_billed = sum(r.billed_cost for r in reconciliations)
    total_discrepancy = total_billed - total_observed
    discrepancy_pct = (
        abs(total_discrepancy) / total_billed if total_billed > 0
        else (1.0 if total_observed > 0 else 0.0)
    )

    attestation_hash = _hash_reconciliations(reconciliations)

    return BillingAttestation(
        org_id=org_id,
        period_start=period_start,
        period_end=period_end,
        reconciliations=reconciliations,
        total_observed_cost=round(total_observed, 6),
        total_billed_cost=round(total_billed, 6),
        total_discrepancy=round(total_discrepancy, 6),
        discrepancy_pct=round(discrepancy_pct, 6),
        attestation_hash=attestation_hash,
    )


async def submit_billing_attestation(
    attestation: BillingAttestation,
    provider: AttestationProvider,
) -> str:
    """Submit a billing attestation's hash to the on-chain provider.

    Constructs an AttestationRecord from the billing attestation and
    submits it. Returns the transaction/record ID from the provider.

    This is a convenience wrapper — callers who need more control over
    the AttestationRecord fields (prev_hash, nonce) should build the
    record directly.
    """
    from blockthrough.attestation.hashing import hash_org_id
    from blockthrough.attestation.types import AttestationRecord

    record = AttestationRecord(
        org_id_hash=hash_org_id(attestation.org_id),
        period_start=attestation.period_start,
        period_end=attestation.period_end,
        metrics_hash=attestation.attestation_hash,
        # Billing attestations don't carry benchmark or Merkle data
        benchmark_hash="0" * 64,
        merkle_root="0" * 64,
        prev_hash="0" * 64,
        nonce=1,
        timestamp=attestation.period_end,
    )

    return await provider.submit(record)
