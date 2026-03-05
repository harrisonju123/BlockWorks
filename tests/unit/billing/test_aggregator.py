"""Tests for the billing usage aggregator.

Mocks the async DB session to verify the aggregator correctly builds
ProviderUsage records from llm_events query results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from blockthrough.billing.aggregator import aggregate_usage


def _make_row(
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    total_prompt_tokens: int = 500_000,
    total_completion_tokens: int = 150_000,
    total_cost: float = 3.75,
    request_count: int = 1000,
) -> dict:
    return {
        "provider": provider,
        "model": model,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cost": total_cost,
        "request_count": request_count,
    }


def _make_session(rows: list[dict]) -> AsyncMock:
    """Build a mock AsyncSession whose execute() returns the given rows."""
    mock_rows = []
    for row in rows:
        mock_mapping = MagicMock()
        mock_mapping._mapping = row
        mock_rows.append(mock_mapping)

    mock_result = MagicMock()
    mock_result.fetchall.return_value = mock_rows

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    return session


START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END = datetime(2026, 4, 1, tzinfo=timezone.utc)


class TestAggregateUsage:

    @pytest.mark.asyncio
    async def test_returns_provider_usage_per_row(self) -> None:
        rows = [
            _make_row(provider="anthropic", model="claude-sonnet-4-20250514"),
            _make_row(provider="openai", model="gpt-4o"),
        ]
        session = _make_session(rows)

        result = await aggregate_usage(session, org_id="test-org", start=START, end=END)

        assert len(result) == 2
        assert result[0].provider == "anthropic"
        assert result[0].model == "claude-sonnet-4-20250514"
        assert result[1].provider == "openai"
        assert result[1].model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_maps_fields_correctly(self) -> None:
        rows = [
            _make_row(
                total_prompt_tokens=1_000_000,
                total_completion_tokens=300_000,
                total_cost=24.75,
                request_count=5000,
            ),
        ]
        session = _make_session(rows)

        result = await aggregate_usage(session, org_id=None, start=START, end=END)

        usage = result[0]
        assert usage.observed_prompt_tokens == 1_000_000
        assert usage.observed_completion_tokens == 300_000
        assert usage.observed_cost == 24.75
        assert usage.observed_request_count == 5000
        assert usage.period_start == START
        assert usage.period_end == END

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        session = _make_session([])
        result = await aggregate_usage(session, org_id="test-org", start=START, end=END)
        assert result == []

    @pytest.mark.asyncio
    async def test_null_values_default_to_zero(self) -> None:
        """DB returns NULL for SUM/COUNT when no rows match the group."""
        rows = [
            _make_row(
                total_prompt_tokens=0,
                total_completion_tokens=0,
                total_cost=0,
                request_count=0,
            ),
        ]
        session = _make_session(rows)

        result = await aggregate_usage(session, org_id="test-org", start=START, end=END)

        usage = result[0]
        assert usage.observed_prompt_tokens == 0
        assert usage.observed_completion_tokens == 0
        assert usage.observed_cost == 0.0
        assert usage.observed_request_count == 0

    @pytest.mark.asyncio
    async def test_org_id_none_omits_filter(self) -> None:
        """When org_id is None, the query should not include an org_id filter."""
        session = _make_session([])
        await aggregate_usage(session, org_id=None, start=START, end=END)

        # Inspect the SQL that was executed
        call_args = session.execute.call_args
        sql_text = str(call_args[0][0])
        params = call_args[0][1]
        assert "org_id" not in params
        assert "org_id = :org_id" not in sql_text

    @pytest.mark.asyncio
    async def test_org_id_present_includes_filter(self) -> None:
        """When org_id is set, it should appear in both the SQL and params."""
        session = _make_session([])
        await aggregate_usage(session, org_id="acme-corp", start=START, end=END)

        call_args = session.execute.call_args
        params = call_args[0][1]
        assert params["org_id"] == "acme-corp"
