"""Reconciliation engine — compares observed usage against provider invoices.

Matches observed ProviderUsage records against InvoiceData line items by
(provider, model), computes token and cost discrepancies, and flags
anything exceeding the notable threshold (default 2%).

When invoice data lacks model-level granularity (model=None), the engine
falls back to provider-level matching: all observed usage for that provider
is summed and compared against the aggregate invoice.
"""

from __future__ import annotations

from agentproof.billing.types import InvoiceData, ProviderUsage, ReconciliationResult

# Discrepancies above this percentage are flagged as notable
NOTABLE_THRESHOLD_PCT = 0.02


def reconcile(
    observed: list[ProviderUsage],
    billed: list[InvoiceData],
    *,
    threshold_pct: float = NOTABLE_THRESHOLD_PCT,
) -> list[ReconciliationResult]:
    """Compare observed usage against billed invoice data.

    Matching strategy:
    1. If an invoice line has a model, match by (provider, model).
    2. If an invoice line has model=None, aggregate all observed usage
       for that provider and compare at the provider level.

    Unmatched observed usage (no corresponding invoice line) is reported
    with zero billed amounts. Unmatched invoice lines (no observed usage)
    are reported with zero observed amounts.
    """
    # Index observed by (provider, model) for O(1) lookup
    observed_by_key: dict[tuple[str, str], ProviderUsage] = {
        (u.provider, u.model): u for u in observed
    }

    # Aggregate observed by provider for provider-level fallback
    observed_by_provider: dict[str, _AggregatedUsage] = {}
    for u in observed:
        if u.provider not in observed_by_provider:
            observed_by_provider[u.provider] = _AggregatedUsage()
        observed_by_provider[u.provider].add(u)

    results: list[ReconciliationResult] = []
    matched_observed_keys: set[tuple[str, str]] = set()

    for invoice in billed:
        if invoice.model is not None:
            # Model-level matching
            key = (invoice.provider, invoice.model)
            usage = observed_by_key.get(key)
            matched_observed_keys.add(key)

            obs_prompt = usage.observed_prompt_tokens if usage else 0
            obs_completion = usage.observed_completion_tokens if usage else 0
            obs_cost = usage.observed_cost if usage else 0.0

            result = _build_result(
                provider=invoice.provider,
                model=invoice.model,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                obs_prompt=obs_prompt,
                obs_completion=obs_completion,
                obs_cost=obs_cost,
                billed_prompt=invoice.billed_prompt_tokens,
                billed_completion=invoice.billed_completion_tokens,
                billed_cost=invoice.billed_cost,
                threshold_pct=threshold_pct,
            )
            results.append(result)
        else:
            # Provider-level fallback: no model granularity on the invoice
            agg = observed_by_provider.get(invoice.provider)
            obs_prompt = agg.prompt_tokens if agg else 0
            obs_completion = agg.completion_tokens if agg else 0
            obs_cost = agg.cost if agg else 0.0

            # Mark all observed keys for this provider as matched
            for key in observed_by_key:
                if key[0] == invoice.provider:
                    matched_observed_keys.add(key)

            result = _build_result(
                provider=invoice.provider,
                model=None,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                obs_prompt=obs_prompt,
                obs_completion=obs_completion,
                obs_cost=obs_cost,
                billed_prompt=invoice.billed_prompt_tokens,
                billed_completion=invoice.billed_completion_tokens,
                billed_cost=invoice.billed_cost,
                threshold_pct=threshold_pct,
            )
            results.append(result)

    # Report unmatched observed usage (we saw traffic, but no invoice line)
    for key, usage in observed_by_key.items():
        if key not in matched_observed_keys:
            result = _build_result(
                provider=usage.provider,
                model=usage.model,
                period_start=usage.period_start,
                period_end=usage.period_end,
                obs_prompt=usage.observed_prompt_tokens,
                obs_completion=usage.observed_completion_tokens,
                obs_cost=usage.observed_cost,
                billed_prompt=0,
                billed_completion=0,
                billed_cost=0.0,
                threshold_pct=threshold_pct,
            )
            results.append(result)

    return sorted(results, key=lambda r: abs(r.discrepancy_cost), reverse=True)


class _AggregatedUsage:
    """Mutable accumulator for summing observed usage across models within a provider."""

    __slots__ = ("prompt_tokens", "completion_tokens", "cost")

    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.cost: float = 0.0

    def add(self, usage: ProviderUsage) -> None:
        self.prompt_tokens += usage.observed_prompt_tokens
        self.completion_tokens += usage.observed_completion_tokens
        self.cost += usage.observed_cost


def _build_result(
    *,
    provider: str,
    model: str | None,
    period_start,
    period_end,
    obs_prompt: int,
    obs_completion: int,
    obs_cost: float,
    billed_prompt: int,
    billed_completion: int,
    billed_cost: float,
    threshold_pct: float,
) -> ReconciliationResult:
    """Compute discrepancies and build a ReconciliationResult."""
    obs_total = obs_prompt + obs_completion
    billed_total = billed_prompt + billed_completion

    disc_prompt = billed_prompt - obs_prompt
    disc_completion = billed_completion - obs_completion
    disc_total = billed_total - obs_total
    disc_cost = billed_cost - obs_cost

    # Percentage discrepancy relative to billed; 0.0 when billed is zero
    disc_pct_tokens = abs(disc_total) / billed_total if billed_total > 0 else (1.0 if obs_total > 0 else 0.0)
    disc_pct_cost = abs(disc_cost) / billed_cost if billed_cost > 0 else (1.0 if obs_cost > 0 else 0.0)

    is_notable = disc_pct_tokens > threshold_pct or disc_pct_cost > threshold_pct

    return ReconciliationResult(
        provider=provider,
        model=model,
        period_start=period_start,
        period_end=period_end,
        observed_prompt_tokens=obs_prompt,
        observed_completion_tokens=obs_completion,
        observed_total_tokens=obs_total,
        observed_cost=obs_cost,
        billed_prompt_tokens=billed_prompt,
        billed_completion_tokens=billed_completion,
        billed_total_tokens=billed_total,
        billed_cost=billed_cost,
        discrepancy_prompt_tokens=disc_prompt,
        discrepancy_completion_tokens=disc_completion,
        discrepancy_total_tokens=disc_total,
        discrepancy_cost=disc_cost,
        discrepancy_pct_tokens=round(disc_pct_tokens, 6),
        discrepancy_pct_cost=round(disc_pct_cost, 6),
        is_notable=is_notable,
    )
