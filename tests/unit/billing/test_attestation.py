"""Tests for billing attestation generation.

Verifies that create_billing_attestation correctly computes aggregate
totals, deterministic hashes, and discrepancy percentages from
reconciliation results.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blockthrough.billing.attestation import (
    _hash_reconciliations,
    create_billing_attestation,
    submit_billing_attestation,
)
from blockthrough.billing.types import ReconciliationResult

START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _reconciliation(
    provider: str = "anthropic",
    model: str | None = "claude-sonnet-4-20250514",
    observed_cost: float = 100.0,
    billed_cost: float = 105.0,
    observed_prompt: int = 1_000_000,
    observed_completion: int = 300_000,
    billed_prompt: int = 1_050_000,
    billed_completion: int = 315_000,
) -> ReconciliationResult:
    obs_total = observed_prompt + observed_completion
    billed_total = billed_prompt + billed_completion
    disc_total = billed_total - obs_total
    disc_cost = billed_cost - observed_cost
    disc_pct_tokens = abs(disc_total) / billed_total if billed_total > 0 else 0.0
    disc_pct_cost = abs(disc_cost) / billed_cost if billed_cost > 0 else 0.0

    return ReconciliationResult(
        provider=provider,
        model=model,
        period_start=START,
        period_end=END,
        observed_prompt_tokens=observed_prompt,
        observed_completion_tokens=observed_completion,
        observed_total_tokens=obs_total,
        observed_cost=observed_cost,
        billed_prompt_tokens=billed_prompt,
        billed_completion_tokens=billed_completion,
        billed_total_tokens=billed_total,
        billed_cost=billed_cost,
        discrepancy_prompt_tokens=billed_prompt - observed_prompt,
        discrepancy_completion_tokens=billed_completion - observed_completion,
        discrepancy_total_tokens=disc_total,
        discrepancy_cost=disc_cost,
        discrepancy_pct_tokens=round(disc_pct_tokens, 6),
        discrepancy_pct_cost=round(disc_pct_cost, 6),
        is_notable=disc_pct_cost > 0.02,
    )


class TestCreateBillingAttestation:

    def test_aggregate_totals(self) -> None:
        recs = [
            _reconciliation(provider="anthropic", observed_cost=100.0, billed_cost=105.0),
            _reconciliation(provider="openai", model="gpt-4o", observed_cost=50.0, billed_cost=52.0),
        ]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)

        assert att.total_observed_cost == pytest.approx(150.0)
        assert att.total_billed_cost == pytest.approx(157.0)
        assert att.total_discrepancy == pytest.approx(7.0)

    def test_discrepancy_percentage(self) -> None:
        recs = [
            _reconciliation(observed_cost=100.0, billed_cost=110.0),
        ]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)

        expected_pct = 10.0 / 110.0
        assert att.discrepancy_pct == pytest.approx(expected_pct, abs=1e-5)

    def test_attestation_hash_is_64_char_hex(self) -> None:
        recs = [_reconciliation()]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)

        assert len(att.attestation_hash) == 64
        int(att.attestation_hash, 16)

    def test_deterministic_hash(self) -> None:
        recs = [_reconciliation(), _reconciliation(provider="openai", model="gpt-4o")]
        att1 = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)
        att2 = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)
        assert att1.attestation_hash == att2.attestation_hash

    def test_hash_order_independent(self) -> None:
        """Input ordering of reconciliations shouldn't affect the hash."""
        rec_a = _reconciliation(provider="anthropic")
        rec_b = _reconciliation(provider="openai", model="gpt-4o", observed_cost=50.0, billed_cost=52.0)

        att1 = create_billing_attestation([rec_a, rec_b], org_id="test-org", period_start=START, period_end=END)
        att2 = create_billing_attestation([rec_b, rec_a], org_id="test-org", period_start=START, period_end=END)
        assert att1.attestation_hash == att2.attestation_hash

    def test_different_data_different_hash(self) -> None:
        att1 = create_billing_attestation(
            [_reconciliation(observed_cost=100.0, billed_cost=105.0)],
            org_id="test-org", period_start=START, period_end=END,
        )
        att2 = create_billing_attestation(
            [_reconciliation(observed_cost=100.0, billed_cost=200.0)],
            org_id="test-org", period_start=START, period_end=END,
        )
        assert att1.attestation_hash != att2.attestation_hash

    def test_empty_reconciliations(self) -> None:
        att = create_billing_attestation([], org_id="test-org", period_start=START, period_end=END)

        assert att.total_observed_cost == 0.0
        assert att.total_billed_cost == 0.0
        assert att.total_discrepancy == 0.0
        assert att.discrepancy_pct == 0.0
        assert len(att.attestation_hash) == 64

    def test_preserves_org_and_period(self) -> None:
        att = create_billing_attestation(
            [_reconciliation()], org_id="acme-corp", period_start=START, period_end=END,
        )
        assert att.org_id == "acme-corp"
        assert att.period_start == START
        assert att.period_end == END

    def test_reconciliations_included(self) -> None:
        recs = [_reconciliation(), _reconciliation(provider="openai", model="gpt-4o")]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)
        assert len(att.reconciliations) == 2

    def test_zero_billed_discrepancy_pct(self) -> None:
        """When billed is zero but observed is nonzero, pct should be 1.0."""
        recs = [_reconciliation(observed_cost=50.0, billed_cost=0.0)]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)
        assert att.discrepancy_pct == 1.0

    def test_both_zero_discrepancy_pct(self) -> None:
        """When both billed and observed are zero, pct should be 0.0."""
        recs = [_reconciliation(observed_cost=0.0, billed_cost=0.0)]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)
        assert att.discrepancy_pct == 0.0


class TestHashReconciliations:
    """Direct tests for the internal hashing function."""

    def test_deterministic(self) -> None:
        recs = [_reconciliation()]
        assert _hash_reconciliations(recs) == _hash_reconciliations(recs)

    def test_sort_independence(self) -> None:
        a = _reconciliation(provider="anthropic")
        b = _reconciliation(provider="openai", model="gpt-4o")
        assert _hash_reconciliations([a, b]) == _hash_reconciliations([b, a])

    def test_empty_list(self) -> None:
        result = _hash_reconciliations([])
        assert len(result) == 64


class TestSubmitBillingAttestation:
    """Verify the submission wrapper constructs a valid record."""

    @pytest.mark.asyncio
    async def test_submit_calls_provider(self) -> None:
        from unittest.mock import AsyncMock

        recs = [_reconciliation()]
        att = create_billing_attestation(recs, org_id="test-org", period_start=START, period_end=END)

        mock_provider = AsyncMock()
        mock_provider.submit = AsyncMock(return_value="local-tx-00000001")

        tx_id = await submit_billing_attestation(att, mock_provider)

        assert tx_id == "local-tx-00000001"
        mock_provider.submit.assert_awaited_once()

        # Verify the submitted record has the attestation hash as metrics_hash
        submitted_record = mock_provider.submit.call_args[0][0]
        assert submitted_record.metrics_hash == att.attestation_hash
        assert len(submitted_record.org_id_hash) == 64

    @pytest.mark.asyncio
    async def test_submit_uses_zero_hashes_for_non_billing_fields(self) -> None:
        """Billing attestations don't carry benchmark or merkle data."""
        from unittest.mock import AsyncMock

        att = create_billing_attestation(
            [_reconciliation()], org_id="test-org", period_start=START, period_end=END,
        )

        mock_provider = AsyncMock()
        mock_provider.submit = AsyncMock(return_value="tx-1")

        await submit_billing_attestation(att, mock_provider)

        submitted = mock_provider.submit.call_args[0][0]
        assert submitted.benchmark_hash == "0" * 64
        assert submitted.merkle_root == "0" * 64
