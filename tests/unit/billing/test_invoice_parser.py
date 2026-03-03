"""Tests for invoice parsers — Anthropic and OpenAI format normalization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentproof.billing.invoice_parser import parse_anthropic_invoice, parse_openai_invoice


PERIOD_START = "2026-03-01T00:00:00Z"
PERIOD_END = "2026-04-01T00:00:00Z"


def _anthropic_payload(
    *,
    invoice_id: str | None = "inv_abc123",
    line_items: list[dict] | None = None,
) -> dict:
    payload: dict = {
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
    }
    if invoice_id is not None:
        payload["invoice_id"] = invoice_id
    if line_items is not None:
        payload["line_items"] = line_items
    else:
        payload["line_items"] = [
            {
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 5_000_000,
                "output_tokens": 1_500_000,
                "cost": 24.75,
            },
        ]
    return payload


def _openai_payload(
    *,
    invoice_id: str | None = "inv_xyz789",
    line_items: list[dict] | None = None,
) -> dict:
    payload: dict = {
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
    }
    if invoice_id is not None:
        payload["invoice_id"] = invoice_id
    if line_items is not None:
        payload["line_items"] = line_items
    else:
        payload["line_items"] = [
            {
                "model": "gpt-4o",
                "prompt_tokens": 3_000_000,
                "completion_tokens": 900_000,
                "cost": 10.50,
            },
        ]
    return payload


class TestParseAnthropicInvoice:

    def test_basic_parsing(self) -> None:
        result = parse_anthropic_invoice(_anthropic_payload())
        assert len(result) == 1

        item = result[0]
        assert item.provider == "anthropic"
        assert item.model == "claude-sonnet-4-20250514"
        assert item.billed_prompt_tokens == 5_000_000
        assert item.billed_completion_tokens == 1_500_000
        assert item.billed_cost == 24.75
        assert item.invoice_id == "inv_abc123"

    def test_period_parsing(self) -> None:
        result = parse_anthropic_invoice(_anthropic_payload())
        item = result[0]
        assert item.period_start == datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert item.period_end == datetime(2026, 4, 1, tzinfo=timezone.utc)

    def test_multiple_line_items(self) -> None:
        payload = _anthropic_payload(line_items=[
            {"model": "claude-sonnet-4-20250514", "input_tokens": 1000, "output_tokens": 500, "cost": 1.0},
            {"model": "claude-haiku-4-5-20251001", "input_tokens": 2000, "output_tokens": 800, "cost": 0.5},
        ])
        result = parse_anthropic_invoice(payload)
        assert len(result) == 2
        assert result[0].model == "claude-sonnet-4-20250514"
        assert result[1].model == "claude-haiku-4-5-20251001"

    def test_no_line_items(self) -> None:
        payload = _anthropic_payload(line_items=[])
        result = parse_anthropic_invoice(payload)
        assert result == []

    def test_missing_line_items_key(self) -> None:
        """Gracefully handle missing line_items — treats as empty list."""
        payload = {"period_start": PERIOD_START, "period_end": PERIOD_END}
        result = parse_anthropic_invoice(payload)
        assert result == []

    def test_no_invoice_id(self) -> None:
        payload = _anthropic_payload(invoice_id=None)
        result = parse_anthropic_invoice(payload)
        assert result[0].invoice_id is None

    def test_model_none_when_missing(self) -> None:
        """Line item without a model key should produce model=None."""
        payload = _anthropic_payload(line_items=[
            {"input_tokens": 1000, "output_tokens": 500, "cost": 1.0},
        ])
        result = parse_anthropic_invoice(payload)
        assert result[0].model is None

    def test_missing_token_fields_default_to_zero(self) -> None:
        payload = _anthropic_payload(line_items=[
            {"model": "claude-sonnet-4-20250514", "cost": 5.0},
        ])
        result = parse_anthropic_invoice(payload)
        assert result[0].billed_prompt_tokens == 0
        assert result[0].billed_completion_tokens == 0

    def test_naive_datetime_gets_utc(self) -> None:
        """ISO datetime without timezone info should default to UTC."""
        payload = _anthropic_payload()
        payload["period_start"] = "2026-03-01T00:00:00"
        result = parse_anthropic_invoice(payload)
        assert result[0].period_start.tzinfo == timezone.utc


class TestParseOpenAIInvoice:

    def test_basic_parsing(self) -> None:
        result = parse_openai_invoice(_openai_payload())
        assert len(result) == 1

        item = result[0]
        assert item.provider == "openai"
        assert item.model == "gpt-4o"
        assert item.billed_prompt_tokens == 3_000_000
        assert item.billed_completion_tokens == 900_000
        assert item.billed_cost == 10.50
        assert item.invoice_id == "inv_xyz789"

    def test_uses_prompt_completion_naming(self) -> None:
        """OpenAI uses 'prompt_tokens' / 'completion_tokens' (not input/output)."""
        payload = _openai_payload(line_items=[
            {"model": "gpt-4o-mini", "prompt_tokens": 7777, "completion_tokens": 3333, "cost": 0.01},
        ])
        result = parse_openai_invoice(payload)
        assert result[0].billed_prompt_tokens == 7777
        assert result[0].billed_completion_tokens == 3333

    def test_multiple_models(self) -> None:
        payload = _openai_payload(line_items=[
            {"model": "gpt-4o", "prompt_tokens": 1000, "completion_tokens": 500, "cost": 5.0},
            {"model": "gpt-4o-mini", "prompt_tokens": 2000, "completion_tokens": 800, "cost": 0.5},
        ])
        result = parse_openai_invoice(payload)
        assert len(result) == 2

    def test_empty_line_items(self) -> None:
        payload = _openai_payload(line_items=[])
        result = parse_openai_invoice(payload)
        assert result == []
