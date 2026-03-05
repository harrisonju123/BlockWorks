"""Tests for MCP type models."""

import uuid
from datetime import datetime, timezone

from blockthrough.mcp.types import MCPCall, MCPExecutionEdge, MCPServerStats
from blockthrough.types import EventStatus


class TestMCPCall:
    def test_minimal_creation(self):
        call = MCPCall(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            trace_id="trace-1",
            created_at=datetime.now(timezone.utc),
            server_name="filesystem",
            method="read_file",
            params_hash="abc123",
        )
        assert call.status == EventStatus.SUCCESS
        assert call.response_hash is None
        assert call.latency_ms is None
        assert call.response_tokens is None
        assert call.error_type is None

    def test_full_creation(self):
        call = MCPCall(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            trace_id="trace-1",
            created_at=datetime.now(timezone.utc),
            server_name="database",
            method="query",
            params_hash="abc123",
            response_hash="def456",
            latency_ms=42.5,
            response_tokens=200,
            status=EventStatus.FAILURE,
            error_type="TimeoutError",
        )
        assert call.status == EventStatus.FAILURE
        assert call.latency_ms == 42.5
        assert call.response_tokens == 200

    def test_serialization_roundtrip(self):
        original = MCPCall(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            trace_id="trace-1",
            created_at=datetime.now(timezone.utc),
            server_name="fs",
            method="read",
            params_hash="hash",
        )
        data = original.model_dump()
        restored = MCPCall(**data)
        assert restored.id == original.id
        assert restored.server_name == original.server_name


class TestMCPExecutionEdge:
    def test_creation(self):
        edge = MCPExecutionEdge(
            id=uuid.uuid4(),
            parent_call_id=uuid.uuid4(),
            child_call_id=uuid.uuid4(),
            trace_id="trace-1",
        )
        assert edge.trace_id == "trace-1"
        assert edge.parent_call_id != edge.child_call_id


class TestMCPServerStats:
    def test_creation_with_latency(self):
        stats = MCPServerStats(
            server_name="filesystem",
            call_count=100,
            failure_count=5,
            failure_rate=0.05,
            avg_latency_ms=15.3,
            p50_latency_ms=12.0,
            p95_latency_ms=45.0,
        )
        assert stats.failure_rate == 0.05
        assert stats.p95_latency_ms == 45.0

    def test_creation_without_latency(self):
        stats = MCPServerStats(
            server_name="test",
            call_count=0,
            failure_count=0,
            failure_rate=0.0,
        )
        assert stats.avg_latency_ms is None
        assert stats.p50_latency_ms is None
        assert stats.p95_latency_ms is None
