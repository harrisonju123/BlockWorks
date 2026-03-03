"""Tests for the reconciliation engine.

Covers exact matches, discrepancy calculation, provider-level fallback,
threshold flagging, unmatched records, and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentproof.billing.reconciler import NOTABLE_THRESHOLD_PCT, reconcile
from agentproof.billing.types import InvoiceData, ProviderUsage

START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _usage(
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    prompt_tokens: int = 1_000_000,
    completion_tokens: int = 300_000,
    cost: float = 24.75,
    request_count: int = 5000,
) -> ProviderUsage:
    return ProviderUsage(
        provider=provider,
        model=model,
        period_start=START,
        period_end=END,
        observed_prompt_tokens=prompt_tokens,
        observed_completion_tokens=completion_tokens,
        observed_cost=cost,
        observed_request_count=request_count,
    )


def _invoice(
    provider: str = "anthropic",
    model: str | None = "claude-sonnet-4-20250514",
    prompt_tokens: int = 1_000_000,
    completion_tokens: int = 300_000,
    cost: float = 24.75,
    invoice_id: str | None = None,
) -> InvoiceData:
    return InvoiceData(
        provider=provider,
        model=model,
        period_start=START,
        period_end=END,
        billed_prompt_tokens=prompt_tokens,
        billed_completion_tokens=completion_tokens,
        billed_cost=cost,
        invoice_id=invoice_id,
    )


class TestExactMatch:
    """When observed and billed are identical, discrepancies should be zero."""

    def test_perfect_match_no_discrepancy(self) -> None:
        observed = [_usage()]
        billed = [_invoice()]
        results = reconcile(observed, billed)

        assert len(results) == 1
        r = results[0]
        assert r.discrepancy_prompt_tokens == 0
        assert r.discrepancy_completion_tokens == 0
        assert r.discrepancy_total_tokens == 0
        assert r.discrepancy_cost == 0.0
        assert r.discrepancy_pct_tokens == 0.0
        assert r.discrepancy_pct_cost == 0.0
        assert r.is_notable is False

    def test_perfect_match_preserves_fields(self) -> None:
        observed = [_usage(prompt_tokens=500, completion_tokens=200, cost=1.50)]
        billed = [_invoice(prompt_tokens=500, completion_tokens=200, cost=1.50)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.provider == "anthropic"
        assert r.model == "claude-sonnet-4-20250514"
        assert r.observed_prompt_tokens == 500
        assert r.observed_completion_tokens == 200
        assert r.observed_total_tokens == 700
        assert r.billed_prompt_tokens == 500
        assert r.billed_completion_tokens == 200
        assert r.billed_total_tokens == 700


class TestDiscrepancyCalculation:
    """Verify the math behind token and cost discrepancies."""

    def test_provider_overbilled_tokens(self) -> None:
        """Provider billed more tokens than we observed -> positive discrepancy."""
        observed = [_usage(prompt_tokens=1000, completion_tokens=300)]
        billed = [_invoice(prompt_tokens=1100, completion_tokens=350)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.discrepancy_prompt_tokens == 100  # 1100 - 1000
        assert r.discrepancy_completion_tokens == 50  # 350 - 300
        assert r.discrepancy_total_tokens == 150

    def test_provider_underbilled_tokens(self) -> None:
        """Provider billed fewer tokens than we observed -> negative discrepancy."""
        observed = [_usage(prompt_tokens=1100, completion_tokens=350)]
        billed = [_invoice(prompt_tokens=1000, completion_tokens=300)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.discrepancy_prompt_tokens == -100
        assert r.discrepancy_completion_tokens == -50
        assert r.discrepancy_total_tokens == -150

    def test_cost_discrepancy(self) -> None:
        observed = [_usage(cost=100.0)]
        billed = [_invoice(cost=105.0)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.discrepancy_cost == pytest.approx(5.0)

    def test_percentage_relative_to_billed(self) -> None:
        """Discrepancy percentage is |delta| / billed."""
        observed = [_usage(prompt_tokens=980, completion_tokens=290, cost=24.0)]
        billed = [_invoice(prompt_tokens=1000, completion_tokens=300, cost=25.0)]
        results = reconcile(observed, billed)

        r = results[0]
        expected_token_pct = abs((1300 - 1270)) / 1300  # 30 / 1300
        expected_cost_pct = abs(25.0 - 24.0) / 25.0  # 1.0 / 25.0
        assert r.discrepancy_pct_tokens == pytest.approx(expected_token_pct, abs=1e-5)
        assert r.discrepancy_pct_cost == pytest.approx(expected_cost_pct, abs=1e-5)


class TestNotableThreshold:
    """Discrepancies exceeding the threshold should be flagged."""

    def test_below_threshold_not_flagged(self) -> None:
        """1% discrepancy < 2% threshold -> not notable."""
        observed = [_usage(prompt_tokens=990, completion_tokens=300, cost=24.50)]
        billed = [_invoice(prompt_tokens=1000, completion_tokens=300, cost=24.75)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.is_notable is False

    def test_above_threshold_flagged(self) -> None:
        """5% discrepancy > 2% threshold -> notable."""
        observed = [_usage(prompt_tokens=950, completion_tokens=285, cost=23.50)]
        billed = [_invoice(prompt_tokens=1000, completion_tokens=300, cost=24.75)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.is_notable is True

    def test_custom_threshold(self) -> None:
        """Custom threshold of 10% — 5% discrepancy should NOT be flagged."""
        observed = [_usage(prompt_tokens=950, completion_tokens=285, cost=23.50)]
        billed = [_invoice(prompt_tokens=1000, completion_tokens=300, cost=24.75)]
        results = reconcile(observed, billed, threshold_pct=0.10)

        r = results[0]
        assert r.is_notable is False

    def test_default_threshold_is_two_percent(self) -> None:
        assert NOTABLE_THRESHOLD_PCT == 0.02

    def test_cost_discrepancy_alone_triggers_notable(self) -> None:
        """Even if tokens match, a cost discrepancy > threshold flags it."""
        observed = [_usage(prompt_tokens=1000, completion_tokens=300, cost=24.0)]
        billed = [_invoice(prompt_tokens=1000, completion_tokens=300, cost=25.0)]
        results = reconcile(observed, billed)

        r = results[0]
        # Token discrepancy is 0%, cost discrepancy is 4%
        assert r.discrepancy_pct_tokens == 0.0
        assert r.discrepancy_pct_cost > NOTABLE_THRESHOLD_PCT
        assert r.is_notable is True


class TestProviderLevelFallback:
    """When invoice model is None, match at provider level."""

    def test_aggregates_all_models_for_provider(self) -> None:
        """Two observed models under same provider should be summed for a provider-level invoice."""
        observed = [
            _usage(provider="anthropic", model="claude-sonnet-4-20250514",
                   prompt_tokens=500, completion_tokens=150, cost=10.0),
            _usage(provider="anthropic", model="claude-haiku-4-5-20251001",
                   prompt_tokens=300, completion_tokens=100, cost=2.0),
        ]
        billed = [
            _invoice(provider="anthropic", model=None,
                     prompt_tokens=800, completion_tokens=250, cost=12.0),
        ]
        results = reconcile(observed, billed)

        assert len(results) == 1
        r = results[0]
        assert r.model is None
        assert r.observed_prompt_tokens == 800
        assert r.observed_completion_tokens == 250
        assert r.observed_cost == 12.0
        assert r.discrepancy_total_tokens == 0
        assert r.discrepancy_cost == 0.0

    def test_provider_level_with_discrepancy(self) -> None:
        observed = [
            _usage(provider="openai", model="gpt-4o",
                   prompt_tokens=400, completion_tokens=100, cost=5.0),
        ]
        billed = [
            _invoice(provider="openai", model=None,
                     prompt_tokens=500, completion_tokens=120, cost=6.0),
        ]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.discrepancy_prompt_tokens == 100
        assert r.discrepancy_completion_tokens == 20
        assert r.discrepancy_cost == pytest.approx(1.0)


class TestMultipleProviders:
    """Reconciliation across multiple providers simultaneously."""

    def test_two_providers_matched_independently(self) -> None:
        observed = [
            _usage(provider="anthropic", model="claude-sonnet-4-20250514",
                   prompt_tokens=1000, completion_tokens=300, cost=10.0),
            _usage(provider="openai", model="gpt-4o",
                   prompt_tokens=2000, completion_tokens=600, cost=8.0),
        ]
        billed = [
            _invoice(provider="anthropic", model="claude-sonnet-4-20250514",
                     prompt_tokens=1000, completion_tokens=300, cost=10.0),
            _invoice(provider="openai", model="gpt-4o",
                     prompt_tokens=2100, completion_tokens=630, cost=8.50),
        ]
        results = reconcile(observed, billed)

        assert len(results) == 2
        # Results sorted by abs(discrepancy_cost) desc
        anthropic_r = next(r for r in results if r.provider == "anthropic")
        openai_r = next(r for r in results if r.provider == "openai")
        assert anthropic_r.discrepancy_cost == 0.0
        assert openai_r.discrepancy_cost == pytest.approx(0.50)


class TestUnmatchedRecords:
    """Edge cases: observed usage with no invoice, or invoice with no observed usage."""

    def test_unmatched_observed_reported_with_zero_billed(self) -> None:
        """We saw traffic but got no invoice line -> billed amounts are zero."""
        observed = [_usage(provider="anthropic", model="claude-sonnet-4-20250514")]
        billed = []  # no invoice at all
        results = reconcile(observed, billed)

        assert len(results) == 1
        r = results[0]
        assert r.observed_prompt_tokens == 1_000_000
        assert r.billed_prompt_tokens == 0
        assert r.billed_cost == 0.0
        # When billed is zero but observed is nonzero, pct = 1.0
        assert r.discrepancy_pct_cost == 1.0
        assert r.is_notable is True

    def test_unmatched_invoice_reported_with_zero_observed(self) -> None:
        """Invoice line with no corresponding observed usage -> observed amounts are zero."""
        observed = []
        billed = [_invoice(provider="anthropic", model="claude-sonnet-4-20250514")]
        results = reconcile(observed, billed)

        assert len(results) == 1
        r = results[0]
        assert r.observed_prompt_tokens == 0
        assert r.billed_prompt_tokens == 1_000_000
        assert r.observed_cost == 0.0
        assert r.billed_cost == 24.75


class TestEmptyInputs:

    def test_both_empty(self) -> None:
        results = reconcile([], [])
        assert results == []

    def test_empty_observed_only(self) -> None:
        billed = [_invoice()]
        results = reconcile([], billed)
        assert len(results) == 1
        assert results[0].observed_total_tokens == 0

    def test_empty_billed_only(self) -> None:
        observed = [_usage()]
        results = reconcile(observed, [])
        assert len(results) == 1
        assert results[0].billed_total_tokens == 0


class TestSortOrder:
    """Results should be sorted by absolute discrepancy cost, descending."""

    def test_sorted_by_abs_discrepancy_cost(self) -> None:
        observed = [
            _usage(provider="a", model="m1", cost=10.0),
            _usage(provider="b", model="m2", cost=50.0),
            _usage(provider="c", model="m3", cost=30.0),
        ]
        billed = [
            _invoice(provider="a", model="m1", cost=10.5),  # disc = 0.5
            _invoice(provider="b", model="m2", cost=55.0),  # disc = 5.0
            _invoice(provider="c", model="m3", cost=28.0),  # disc = -2.0
        ]
        results = reconcile(observed, billed)

        abs_costs = [abs(r.discrepancy_cost) for r in results]
        assert abs_costs == sorted(abs_costs, reverse=True)


class TestZeroBilled:
    """Edge case: billed tokens or cost are zero."""

    def test_zero_billed_zero_observed(self) -> None:
        observed = [_usage(prompt_tokens=0, completion_tokens=0, cost=0.0)]
        billed = [_invoice(prompt_tokens=0, completion_tokens=0, cost=0.0)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.discrepancy_pct_tokens == 0.0
        assert r.discrepancy_pct_cost == 0.0
        assert r.is_notable is False

    def test_zero_billed_nonzero_observed(self) -> None:
        """Provider says zero, we observed something -> 100% discrepancy."""
        observed = [_usage(prompt_tokens=1000, completion_tokens=300, cost=5.0)]
        billed = [_invoice(prompt_tokens=0, completion_tokens=0, cost=0.0)]
        results = reconcile(observed, billed)

        r = results[0]
        assert r.discrepancy_pct_tokens == 1.0
        assert r.discrepancy_pct_cost == 1.0
        assert r.is_notable is True
