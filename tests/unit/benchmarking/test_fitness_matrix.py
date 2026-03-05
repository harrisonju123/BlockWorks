"""Tests for the fitness matrix query layer.

Uses a mock AsyncSession to verify query construction and result mapping
without requiring a live database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.db.queries import get_fitness_matrix


def _mock_row(**kwargs) -> MagicMock:
    """Build a mock row that supports dict(row._mapping)."""
    row = MagicMock()
    row._mapping = kwargs
    return row


class TestGetFitnessMatrix:

    @pytest.mark.asyncio
    async def test_returns_fitness_entries_from_aggregate(self) -> None:
        """Without org_id, should query the fitness_matrix continuous aggregate."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                model="claude-haiku-4-5-20251001",
                task_type="code_generation",
                avg_quality=0.85,
                avg_cost=0.001,
                avg_latency=500.0,
                sample_size=100,
            ),
            _mock_row(
                model="gpt-4o-mini",
                task_type="classification",
                avg_quality=0.92,
                avg_cost=0.0005,
                avg_latency=300.0,
                sample_size=200,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        entries = await get_fitness_matrix(session, org_id=None)

        assert len(entries) == 2
        assert all(isinstance(e, FitnessEntry) for e in entries)

        # Verify first entry
        assert entries[0].model == "claude-haiku-4-5-20251001"
        assert entries[0].task_type == "code_generation"
        assert entries[0].avg_quality == pytest.approx(0.85)
        assert entries[0].avg_cost == pytest.approx(0.001)
        assert entries[0].avg_latency == pytest.approx(500.0)
        assert entries[0].sample_size == 100

        # Verify second entry
        assert entries[1].model == "gpt-4o-mini"
        assert entries[1].task_type == "classification"
        assert entries[1].sample_size == 200

        # Verify the query was against the aggregate view (no org_id filter)
        call_args = session.execute.call_args
        query_text = str(call_args.args[0].text)
        assert "fitness_matrix" in query_text
        assert "org_id" not in query_text

    @pytest.mark.asyncio
    async def test_org_scoped_queries_raw_table(self) -> None:
        """With org_id, should fall back to benchmark_results for org-level filtering."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                model="gpt-4o-mini",
                task_type="summarization",
                avg_quality=0.88,
                avg_cost=0.0008,
                avg_latency=400.0,
                sample_size=50,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        entries = await get_fitness_matrix(session, org_id="org_acme")

        assert len(entries) == 1
        assert entries[0].model == "gpt-4o-mini"

        # Verify the query was against benchmark_results with org_id filter
        call_args = session.execute.call_args
        query_text = str(call_args.args[0].text)
        assert "benchmark_results" in query_text
        assert "org_id" in query_text
        params = call_args.args[1]
        assert params["org_id"] == "org_acme"

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        session = AsyncMock()
        session.execute.return_value = mock_result

        entries = await get_fitness_matrix(session)
        assert entries == []

    @pytest.mark.asyncio
    async def test_null_values_default_to_zero(self) -> None:
        """DB NULLs should be coerced to 0 rather than raising."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            _mock_row(
                model="test-model",
                task_type="extraction",
                avg_quality=None,
                avg_cost=None,
                avg_latency=None,
                sample_size=None,
            ),
        ]

        session = AsyncMock()
        session.execute.return_value = mock_result

        entries = await get_fitness_matrix(session)
        assert len(entries) == 1
        assert entries[0].avg_quality == 0.0
        assert entries[0].avg_cost == 0.0
        assert entries[0].avg_latency == 0.0
        assert entries[0].sample_size == 0
