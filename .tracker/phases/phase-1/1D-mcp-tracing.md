# 1D — MCP Server Tracing

**Status:** not started
**Owner:** be1
**Target:** Weeks 6–10
**Dependencies:** 0A (data pipeline)
**Blocks:** none (unique differentiator)

## Objective

Capture MCP tool invocations with full execution graph mapping. Identify slow, failing, or wasteful MCP servers. No existing observability tool does this.

## Tasks

- [ ] **1D-1** Extend callback to capture MCP tool invocations (server name, method, params hash, response summary, latency) — `be1`
- [ ] **1D-2** Build execution graph mapper — full DAG of multi-tool workflows (agent → LLM → tool → LLM → tool) — `be1`
- [ ] **1D-3** MCP performance analytics: P50/P95 latency per server, failure rates, unused return data detection — `be1`
- [ ] **1D-4** Cost attribution per MCP call within a trace (token cost of the context that MCP data occupies) — `be1`

## Technical Notes

- MCP tool calls appear in the LLM response as tool_use blocks — intercept at callback level
- Execution graph: store as adjacency list in Postgres, render as DAG in dashboard (Phase 1E/dashboard update)
- "Unused return data": MCP returns 2000 tokens but agent only references 200 → 1800 tokens of wasted context
- This becomes critical for the marketplace (Phase 4) — need to know what MCP servers are worth

## Blockers

_None_
