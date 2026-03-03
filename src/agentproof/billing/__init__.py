"""Billing verification — cross-references observed token usage against provider invoices.

Aggregates independently counted usage from llm_events, parses provider
invoice data, reconciles the two, and generates on-chain billing attestations
for discrepancy transparency.

Public API:
    ProviderUsage         -- independently observed usage per (provider, model)
    InvoiceData           -- provider-reported billing line item
    ReconciliationResult  -- observed vs billed comparison with discrepancy analysis
    BillingAttestation    -- on-chain commitment of reconciliation results
    aggregate_usage       -- query llm_events for observed usage
    parse_anthropic_invoice  -- parse Anthropic usage API response
    parse_openai_invoice     -- parse OpenAI usage API response
    reconcile             -- compare observed vs billed, compute discrepancies
    create_billing_attestation  -- hash reconciliation and submit to attestation provider
"""

from agentproof.billing.aggregator import aggregate_usage
from agentproof.billing.attestation import create_billing_attestation
from agentproof.billing.invoice_parser import parse_anthropic_invoice, parse_openai_invoice
from agentproof.billing.reconciler import reconcile
from agentproof.billing.types import (
    BillingAttestation,
    InvoiceData,
    ProviderUsage,
    ReconciliationResult,
)

__all__ = [
    "BillingAttestation",
    "InvoiceData",
    "ProviderUsage",
    "ReconciliationResult",
    "aggregate_usage",
    "create_billing_attestation",
    "parse_anthropic_invoice",
    "parse_openai_invoice",
    "reconcile",
]
