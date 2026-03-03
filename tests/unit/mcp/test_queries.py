"""Tests for MCP analytics query functions.

Uses a mock AsyncSession to verify SQL construction and result mapping
without requiring a live database.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentproof.db.queries import (
    get_mcp_execution_graph,
    get_mcp_server_stats,
    get_mcp_unused_data,
)


def _make_mapping_row(**kwargs):
    """Create a mock row object that supports ._mapping for dict conversion."""
    row = MagicMock()
    row._mapping = kwargs
    return row


def _mock_session_with_rows(rows):
    """Build a mock AsyncSession whose .execute() returns the given rows."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    session = AsyncMock()
    session.execute.return_value = mock_result
    return session


class TestGetMCPServerStats:
    @pytest.mark.asyncio
    async def test_returns_per_server_stats(self):
        rows = [
            _make_mapping_row(
                server_name="filesystem",
                call_count=50,
                failure_count=2,
                failure_rate=0.04,
                avg_latency_ms=15.3,
                p50_latency_ms=12.0,
                p95_latency_ms=45.0,
            ),
            _make_mapping_row(
                server_name="database",
                call_count=30,
                failure_count=0,
                failure_rate=0.0,
                avg_latency_ms=8.1,
                p50_latency_ms=7.0,
                p95_latency_ms=20.0,
            ),
        ]
        session = _mock_session_with_rows(rows)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)

        result = await get_mcp_server_stats(session, start, end)

        assert len(result) == 2
        assert result[0]["server_name"] == "filesystem"
        assert result[0]["call_count"] == 50
        assert result[1]["server_name"] == "database"

        # Verify the query was executed with correct params
        session.execute.assert_called_once()
        call_args = session.execute.call_args
        params = call_args[0][1]
        assert params["start"] == start
        assert params["end"] == end

    @pytest.mark.asyncio
    async def test_empty_results(self):
        session = _mock_session_with_rows([])
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)

        result = await get_mcp_server_stats(session, start, end)
        assert result == []


class TestGetMCPExecutionGraph:
    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges(self):
        call_id_1 = uuid.uuid4()
        call_id_2 = uuid.uuid4()

        node_rows = [
            _make_mapping_row(
                id=call_id_1,
                event_id=uuid.uuid4(),
                created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                server_name="filesystem",
                method="read_file",
                params_hash="aaa",
                response_hash="bbb",
                latency_ms=10.0,
                response_tokens=50,
                status="success",
                error_type=None,
            ),
            _make_mapping_row(
                id=call_id_2,
                event_id=uuid.uuid4(),
                created_at=datetime(2026, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
                server_name="database",
                method="query",
                params_hash="ccc",
                response_hash="ddd",
                latency_ms=20.0,
                response_tokens=100,
                status="success",
                error_type=None,
            ),
        ]
        edge_rows = [
            _make_mapping_row(
                id=uuid.uuid4(),
                parent_call_id=call_id_1,
                child_call_id=call_id_2,
                trace_id="trace-1",
            ),
        ]

        # Mock session that returns different results for two execute() calls
        node_result = MagicMock()
        node_result.fetchall.return_value = node_rows
        edge_result = MagicMock()
        edge_result.fetchall.return_value = edge_rows

        session = AsyncMock()
        session.execute.side_effect = [node_result, edge_result]

        result = await get_mcp_execution_graph(session, "trace-1")

        assert result["trace_id"] == "trace-1"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["nodes"][0]["server_name"] == "filesystem"
        assert result["edges"][0]["parent_call_id"] == call_id_1

    @pytest.mark.asyncio
    async def test_empty_graph(self):
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []

        session = AsyncMock()
        session.execute.side_effect = [empty_result, empty_result]

        result = await get_mcp_execution_graph(session, "nonexistent-trace")

        assert result["trace_id"] == "nonexistent-trace"
        assert result["nodes"] == []
        assert result["edges"] == []


class TestGetMCPUnusedData:
    @pytest.mark.asyncio
    async def test_returns_waste_data(self):
        rows = [
            _make_mapping_row(
                server_name="filesystem",
                method="read_file",
                unused_call_count=15,
                total_wasted_tokens=3000,
                avg_wasted_tokens=200.0,
            ),
        ]
        session = _mock_session_with_rows(rows)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)

        result = await get_mcp_unused_data(session, start, end)

        assert len(result) == 1
        assert result[0]["server_name"] == "filesystem"
        assert result[0]["total_wasted_tokens"] == 3000

    @pytest.mark.asyncio
    async def test_empty_waste(self):
        session = _mock_session_with_rows([])
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)

        result = await get_mcp_unused_data(session, start, end)
        assert result == []
