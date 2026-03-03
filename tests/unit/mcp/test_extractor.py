"""Tests for MCP call extraction from LLM responses."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agentproof.mcp.extractor import (
    build_execution_graph,
    extract_mcp_calls,
    extract_mcp_calls_from_tool_calls,
    parse_mcp_tool_name,
)
from agentproof.mcp.types import MCPCall
from agentproof.types import EventStatus


class TestParseMCPToolName:
    """Verify the server__method naming convention parser."""

    def test_valid_mcp_name(self):
        result = parse_mcp_tool_name("filesystem__read_file")
        assert result == ("filesystem", "read_file")

    def test_valid_mcp_name_with_dashes(self):
        result = parse_mcp_tool_name("my-server__do-thing")
        assert result == ("my-server", "do-thing")

    def test_regular_tool_name_returns_none(self):
        """Non-MCP tool names (no double underscore) should be ignored."""
        assert parse_mcp_tool_name("get_weather") is None

    def test_single_underscore_returns_none(self):
        assert parse_mcp_tool_name("some_tool_name") is None

    def test_empty_server_returns_none(self):
        assert parse_mcp_tool_name("__method") is None

    def test_empty_method_returns_none(self):
        assert parse_mcp_tool_name("server__") is None

    def test_empty_string_returns_none(self):
        assert parse_mcp_tool_name("") is None

    def test_multiple_double_underscores(self):
        """Only split on the first occurrence."""
        result = parse_mcp_tool_name("server__ns__method")
        assert result == ("server", "ns__method")


class TestExtractMCPCalls:
    """Verify extraction from Anthropic-style tool_use content blocks."""

    def _make_event_id(self):
        return uuid.uuid4()

    def test_extracts_mcp_tool_use_blocks(self):
        event_id = self._make_event_id()
        blocks = [
            {"type": "text", "text": "Let me read that file."},
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "filesystem__read_file",
                "input": {"path": "/tmp/foo.txt"},
            },
            {
                "type": "tool_use",
                "id": "tu_002",
                "name": "database__query",
                "input": {"sql": "SELECT 1"},
            },
        ]

        calls = extract_mcp_calls(blocks, event_id=event_id, trace_id="trace-1")

        assert len(calls) == 2
        assert calls[0].server_name == "filesystem"
        assert calls[0].method == "read_file"
        assert calls[0].event_id == event_id
        assert calls[0].trace_id == "trace-1"
        assert calls[0].status == EventStatus.SUCCESS
        assert len(calls[0].params_hash) == 64  # SHA-256 hex

        assert calls[1].server_name == "database"
        assert calls[1].method == "query"

    def test_ignores_non_mcp_tool_use(self):
        """Regular tool_use blocks (no __ separator) should be skipped."""
        blocks = [
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "get_weather",
                "input": {"city": "NYC"},
            },
        ]

        calls = extract_mcp_calls(
            blocks, event_id=self._make_event_id(), trace_id="trace-1"
        )
        assert len(calls) == 0

    def test_ignores_text_blocks(self):
        blocks = [
            {"type": "text", "text": "Hello"},
        ]
        calls = extract_mcp_calls(
            blocks, event_id=self._make_event_id(), trace_id="trace-1"
        )
        assert len(calls) == 0

    def test_handles_empty_content_blocks(self):
        calls = extract_mcp_calls(
            [], event_id=self._make_event_id(), trace_id="trace-1"
        )
        assert len(calls) == 0

    def test_works_with_object_style_blocks(self):
        """Some SDKs return content blocks as objects with attributes, not dicts."""
        event_id = self._make_event_id()
        block = SimpleNamespace(
            type="tool_use",
            id="tu_001",
            name="github__create_issue",
            input={"title": "Bug", "body": "Something broke"},
        )

        calls = extract_mcp_calls(
            [block], event_id=event_id, trace_id="trace-2"
        )

        assert len(calls) == 1
        assert calls[0].server_name == "github"
        assert calls[0].method == "create_issue"

    def test_uses_provided_created_at(self):
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        blocks = [
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "fs__read",
                "input": {},
            },
        ]
        calls = extract_mcp_calls(
            blocks, event_id=self._make_event_id(), trace_id="t", created_at=ts
        )
        assert calls[0].created_at == ts

    def test_each_call_gets_unique_id(self):
        blocks = [
            {"type": "tool_use", "id": "tu_1", "name": "a__b", "input": {}},
            {"type": "tool_use", "id": "tu_2", "name": "a__c", "input": {}},
        ]
        calls = extract_mcp_calls(
            blocks, event_id=self._make_event_id(), trace_id="t"
        )
        assert calls[0].id != calls[1].id

    def test_deterministic_params_hash(self):
        """Same input should produce the same hash regardless of key order."""
        blocks_a = [
            {"type": "tool_use", "id": "tu_1", "name": "s__m", "input": {"b": 2, "a": 1}},
        ]
        blocks_b = [
            {"type": "tool_use", "id": "tu_1", "name": "s__m", "input": {"a": 1, "b": 2}},
        ]
        eid = self._make_event_id()
        calls_a = extract_mcp_calls(blocks_a, event_id=eid, trace_id="t")
        calls_b = extract_mcp_calls(blocks_b, event_id=eid, trace_id="t")
        assert calls_a[0].params_hash == calls_b[0].params_hash


class TestExtractMCPCallsFromToolCalls:
    """Verify extraction from OpenAI-style tool_calls on message."""

    def test_extracts_mcp_function_calls(self):
        event_id = uuid.uuid4()
        tool_calls = [
            SimpleNamespace(
                id="tc_001",
                type="function",
                function=SimpleNamespace(
                    name="slack__post_message",
                    arguments='{"channel": "#general", "text": "hi"}',
                ),
            ),
        ]

        calls = extract_mcp_calls_from_tool_calls(
            tool_calls, event_id=event_id, trace_id="trace-3"
        )

        assert len(calls) == 1
        assert calls[0].server_name == "slack"
        assert calls[0].method == "post_message"
        assert calls[0].event_id == event_id

    def test_ignores_non_mcp_function_calls(self):
        tool_calls = [
            SimpleNamespace(
                id="tc_001",
                type="function",
                function=SimpleNamespace(
                    name="get_weather",
                    arguments='{"city": "NYC"}',
                ),
            ),
        ]
        calls = extract_mcp_calls_from_tool_calls(
            tool_calls, event_id=uuid.uuid4(), trace_id="t"
        )
        assert len(calls) == 0

    def test_handles_empty_tool_calls(self):
        calls = extract_mcp_calls_from_tool_calls(
            [], event_id=uuid.uuid4(), trace_id="t"
        )
        assert len(calls) == 0


class TestBuildExecutionGraph:
    """Verify DAG construction from ordered MCP calls."""

    def _make_call(self, trace_id: str = "trace-1") -> MCPCall:
        return MCPCall(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            trace_id=trace_id,
            created_at=datetime.now(timezone.utc),
            server_name="test",
            method="method",
            params_hash="abc123",
        )

    def test_single_call_no_edges(self):
        edges = build_execution_graph([self._make_call()])
        assert len(edges) == 0

    def test_two_calls_one_edge(self):
        c1 = self._make_call()
        c2 = self._make_call()
        edges = build_execution_graph([c1, c2])

        assert len(edges) == 1
        assert edges[0].parent_call_id == c1.id
        assert edges[0].child_call_id == c2.id
        assert edges[0].trace_id == "trace-1"

    def test_three_calls_two_edges(self):
        calls = [self._make_call() for _ in range(3)]
        edges = build_execution_graph(calls)

        assert len(edges) == 2
        assert edges[0].parent_call_id == calls[0].id
        assert edges[0].child_call_id == calls[1].id
        assert edges[1].parent_call_id == calls[1].id
        assert edges[1].child_call_id == calls[2].id

    def test_empty_list_no_edges(self):
        edges = build_execution_graph([])
        assert len(edges) == 0

    def test_different_traces_not_linked(self):
        """Calls from different traces should not be connected."""
        c1 = self._make_call(trace_id="trace-a")
        c2 = self._make_call(trace_id="trace-b")
        edges = build_execution_graph([c1, c2])

        assert len(edges) == 0

    def test_each_edge_gets_unique_id(self):
        calls = [self._make_call() for _ in range(3)]
        edges = build_execution_graph(calls)
        assert edges[0].id != edges[1].id
