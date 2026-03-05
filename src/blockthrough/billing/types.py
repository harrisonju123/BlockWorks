"""Billing verification data models.

These types represent the inputs and outputs of the reconciliation pipeline:
provider-observed usage, invoice data from billing APIs, reconciliation
results with discrepancy analysis, and the billing attestation that
commits the reconciliation to on-chain.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderUsage(BaseModel):
    """Independently observed token usage for one (provider, model) pair.

    Aggregated from llm_events over a billing period. This is "our count"
    that we compare against the provider's invoice.
    """

    provider: str
    model: str
    period_start: datetime
    period_end: datetime
    observed_prompt_tokens: int
    observed_completion_tokens: int
    observed_cost: float
    observed_request_count: int


class InvoiceData(BaseModel):
    """Provider-reported billing line item.

    Parsed from provider usage API responses (Anthropic, OpenAI, etc.).
    The model field may be None when the provider doesn't break down
    billing by model — reconciliation falls back to provider-level matching.
    """

    provider: str
    model: str | None = None
    period_start: datetime
    period_end: datetime
    billed_prompt_tokens: int
    billed_completion_tokens: int
    billed_cost: float
    invoice_id: str | None = None


class ReconciliationResult(BaseModel):
    """Comparison of observed vs billed usage for one (provider, model) pair.

    Discrepancy percentages are relative to the billed amount — a positive
    discrepancy means the provider billed more than we observed.
    """

    provider: str
    model: str | None = None
    period_start: datetime
    period_end: datetime

    # Observed (our count)
    observed_prompt_tokens: int
    observed_completion_tokens: int
    observed_total_tokens: int
    observed_cost: float

    # Billed (provider's count)
    billed_prompt_tokens: int
    billed_completion_tokens: int
    billed_total_tokens: int
    billed_cost: float

    # Discrepancies (billed - observed; positive = provider overbilled)
    discrepancy_prompt_tokens: int
    discrepancy_completion_tokens: int
    discrepancy_total_tokens: int
    discrepancy_cost: float

    # Percentage discrepancy relative to billed (0.0 to 1.0+)
    discrepancy_pct_tokens: float
    discrepancy_pct_cost: float

    # True when either token or cost discrepancy exceeds the threshold
    is_notable: bool = False


class BillingAttestation(BaseModel):
    """On-chain billing attestation committing the reconciliation outcome.

    Ties together an org's observed usage, what the provider billed, and
    the computed discrepancy — then hashes the whole thing for on-chain
    anchoring via the attestation provider.
    """

    org_id: str
    period_start: datetime
    period_end: datetime
    reconciliations: list[ReconciliationResult] = Field(default_factory=list)
    total_observed_cost: float
    total_billed_cost: float
    total_discrepancy: float
    discrepancy_pct: float
    attestation_hash: str
