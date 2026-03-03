-- AgentProof MCP Tracing Schema (Phase 1, Initiative 1D)
-- Applied as a migration, not in the init script.
-- Does NOT modify existing schema.sql tables.

-- MCP calls: one row per MCP tool invocation observed in an LLM response.
-- FK-by-convention to llm_events.id (same pattern as tool_calls).
CREATE TABLE mcp_calls (
    id              UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    event_id        UUID NOT NULL,
    trace_id        TEXT NOT NULL,
    server_name     TEXT NOT NULL,
    method          TEXT NOT NULL,
    params_hash     TEXT NOT NULL,
    response_hash   TEXT,
    latency_ms      DOUBLE PRECISION,
    response_tokens INTEGER,
    status          TEXT NOT NULL DEFAULT 'success' CHECK (status IN ('success', 'failure')),
    error_type      TEXT,

    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('mcp_calls', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_mcp_calls_event ON mcp_calls (event_id, created_at DESC);
CREATE INDEX idx_mcp_calls_trace ON mcp_calls (trace_id, created_at DESC);
CREATE INDEX idx_mcp_calls_server ON mcp_calls (server_name, created_at DESC);
CREATE INDEX idx_mcp_calls_server_method ON mcp_calls (server_name, method, created_at DESC);


-- MCP execution graph: DAG edges between MCP calls within a trace.
-- Regular table (not hypertable) because the DAG structure is queried
-- by trace_id, not by time range. Recursive CTEs on hypertables have edge cases.
CREATE TABLE mcp_execution_graph (
    id              UUID NOT NULL PRIMARY KEY,
    parent_call_id  UUID NOT NULL,
    child_call_id   UUID NOT NULL,
    trace_id        TEXT NOT NULL,

    UNIQUE (trace_id, parent_call_id, child_call_id)
);

CREATE INDEX idx_mcp_graph_trace ON mcp_execution_graph (trace_id);
CREATE INDEX idx_mcp_graph_parent ON mcp_execution_graph (parent_call_id);
CREATE INDEX idx_mcp_graph_child ON mcp_execution_graph (child_call_id);


-- Compression and retention for the mcp_calls hypertable
SELECT add_compression_policy('mcp_calls', INTERVAL '7 days');
SELECT add_retention_policy('mcp_calls', INTERVAL '90 days');
