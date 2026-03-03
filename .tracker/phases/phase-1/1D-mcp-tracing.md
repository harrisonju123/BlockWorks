# 1D — MCP Server Tracing

**Status:** done
**Owner:** be1
**Target:** Weeks 6–10
**Dependencies:** 0A (data pipeline)
**Blocks:** none (unique differentiator)

## Objective

Capture MCP tool invocations with full execution graph mapping. Identify slow, failing, or wasteful MCP servers.

## Tasks

- [x] **1D-1** Extend callback to capture MCP tool invocations (server name, method, params hash, response summary, latency) — `be1` (done 2026-03-03)
- [x] **1D-2** Build execution graph mapper — full DAG of multi-tool workflows — `be1` (done 2026-03-03)
- [x] **1D-3** MCP performance analytics: P50/P95 latency per server, failure rates, unused return data detection — `be1` (done 2026-03-03)
- [x] **1D-4** Cost attribution per MCP call within a trace — `be1` (done 2026-03-03)

## Technical Notes

- `schema_mcp.sql`: mcp_calls hypertable + mcp_execution_graph table
- `mcp/extractor.py`: Parses both Anthropic-style tool_use blocks and OpenAI-style function calls
- `mcp/writer.py`: Async MCPWriter with COPY batching, retry logic, graceful shutdown
- 3 API endpoints: /mcp/stats, /mcp/graph/{trace_id}, /mcp/waste
- 37 unit tests covering extraction, DAG construction, analytics queries
- Callback extended with config-gated MCP extraction hook

## Blockers

_None_
