"""Invoice parsers — convert provider usage API responses to InvoiceData.

These are placeholder parsers for now. Real API integration comes later;
these parse the JSON shape that each provider's usage/billing API returns
and normalize it into our InvoiceData model.

Each parser accepts a dict (the deserialized JSON response) and returns
a list of InvoiceData line items.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agentproof.billing.types import InvoiceData


def _parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, defaulting to UTC if no tz info."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_anthropic_invoice(data: dict) -> list[InvoiceData]:
    """Parse Anthropic usage API response into InvoiceData items.

    Expected shape matches Anthropic's billing/usage endpoint:
    {
        "invoice_id": "inv_abc123",  // optional
        "period_start": "2026-03-01T00:00:00Z",
        "period_end": "2026-04-01T00:00:00Z",
        "line_items": [
            {
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 5000000,
                "output_tokens": 1500000,
                "cost": 24.75
            },
            ...
        ]
    }
    """
    period_start = _parse_iso_datetime(data["period_start"])
    period_end = _parse_iso_datetime(data["period_end"])
    invoice_id = data.get("invoice_id")

    results: list[InvoiceData] = []
    for item in data.get("line_items", []):
        results.append(
            InvoiceData(
                provider="anthropic",
                model=item.get("model"),
                period_start=period_start,
                period_end=period_end,
                billed_prompt_tokens=int(item.get("input_tokens", 0)),
                billed_completion_tokens=int(item.get("output_tokens", 0)),
                billed_cost=float(item.get("cost", 0.0)),
                invoice_id=invoice_id,
            )
        )

    return results


def parse_openai_invoice(data: dict) -> list[InvoiceData]:
    """Parse OpenAI usage API response into InvoiceData items.

    Expected shape matches OpenAI's usage/billing endpoint:
    {
        "invoice_id": "inv_xyz789",  // optional
        "period_start": "2026-03-01T00:00:00Z",
        "period_end": "2026-04-01T00:00:00Z",
        "line_items": [
            {
                "model": "gpt-4o",
                "prompt_tokens": 3000000,
                "completion_tokens": 900000,
                "cost": 10.50
            },
            ...
        ]
    }

    OpenAI uses "prompt_tokens" / "completion_tokens" naming, while
    Anthropic uses "input_tokens" / "output_tokens". The parsers
    normalize these into the InvoiceData model.
    """
    period_start = _parse_iso_datetime(data["period_start"])
    period_end = _parse_iso_datetime(data["period_end"])
    invoice_id = data.get("invoice_id")

    results: list[InvoiceData] = []
    for item in data.get("line_items", []):
        results.append(
            InvoiceData(
                provider="openai",
                model=item.get("model"),
                period_start=period_start,
                period_end=period_end,
                billed_prompt_tokens=int(item.get("prompt_tokens", 0)),
                billed_completion_tokens=int(item.get("completion_tokens", 0)),
                billed_cost=float(item.get("cost", 0.0)),
                invoice_id=invoice_id,
            )
        )

    return results
