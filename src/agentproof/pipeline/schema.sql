-- AgentProof TimescaleDB Schema (v1)
-- Applied automatically on first docker compose up via init script.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Core events table
CREATE TABLE llm_events (
    id                      UUID PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('success', 'failure')),

    -- Provider/model
    provider                TEXT NOT NULL,
    model                   TEXT NOT NULL,
    model_group             TEXT,

    -- Tokens
    prompt_tokens           INTEGER NOT NULL,
    completion_tokens       INTEGER NOT NULL,
    total_tokens            INTEGER NOT NULL,

    -- Cost
    estimated_cost          DOUBLE PRECISION NOT NULL,
    custom_pricing          DOUBLE PRECISION,

    -- Latency
    latency_ms              DOUBLE PRECISION NOT NULL,
    time_to_first_token_ms  DOUBLE PRECISION,

    -- Content hashes
    prompt_hash             TEXT NOT NULL,
    completion_hash         TEXT NOT NULL,
    system_prompt_hash      TEXT,

    -- Trace context
    session_id              TEXT,
    trace_id                TEXT NOT NULL,
    span_id                 TEXT NOT NULL,
    parent_span_id          TEXT,

    -- Agent detection
    agent_framework         TEXT,
    agent_name              TEXT,

    -- Tool calls
    has_tool_calls          BOOLEAN NOT NULL DEFAULT FALSE,

    -- Classification
    task_type               TEXT,
    task_type_confidence    DOUBLE PRECISION,

    -- Error
    error_type              TEXT,
    error_message_hash      TEXT,

    -- Metadata
    litellm_call_id         TEXT NOT NULL,
    api_base                TEXT,
    org_id                  TEXT,
    user_id                 TEXT,
    custom_metadata         JSONB
);

-- Convert to hypertable (1-day chunks)
SELECT create_hypertable('llm_events', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

-- Indexes for common query patterns
CREATE INDEX idx_llm_events_trace ON llm_events (trace_id, created_at DESC);
CREATE INDEX idx_llm_events_session ON llm_events (session_id, created_at DESC)
    WHERE session_id IS NOT NULL;
CREATE INDEX idx_llm_events_model ON llm_events (model, created_at DESC);
CREATE INDEX idx_llm_events_provider ON llm_events (provider, created_at DESC);
CREATE INDEX idx_llm_events_task_type ON llm_events (task_type, created_at DESC)
    WHERE task_type IS NOT NULL;
CREATE INDEX idx_llm_events_org ON llm_events (org_id, created_at DESC)
    WHERE org_id IS NOT NULL;
CREATE INDEX idx_llm_events_status ON llm_events (status, created_at DESC)
    WHERE status = 'failure';
CREATE INDEX idx_llm_events_cost ON llm_events (estimated_cost DESC, created_at DESC);

-- Tool calls (normalized)
CREATE TABLE tool_calls (
    id                      UUID PRIMARY KEY,
    event_id                UUID NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL,
    tool_name               TEXT NOT NULL,
    args_hash               TEXT NOT NULL,
    response_summary_hash   TEXT
);

SELECT create_hypertable('tool_calls', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_tool_calls_event ON tool_calls (event_id, created_at DESC);
CREATE INDEX idx_tool_calls_name ON tool_calls (tool_name, created_at DESC);

-- Continuous aggregates for dashboard queries

-- Hourly stats by model
CREATE MATERIALIZED VIEW hourly_model_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', created_at) AS bucket,
    model,
    provider,
    COUNT(*) AS request_count,
    SUM(estimated_cost) AS total_cost,
    AVG(latency_ms) AS avg_latency_ms,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    COUNT(*) FILTER (WHERE status = 'failure') AS failure_count
FROM llm_events
GROUP BY bucket, model, provider
WITH NO DATA;

SELECT add_continuous_aggregate_policy('hourly_model_stats',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Hourly stats by task type
CREATE MATERIALIZED VIEW hourly_task_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', created_at) AS bucket,
    task_type,
    model,
    COUNT(*) AS request_count,
    SUM(estimated_cost) AS total_cost,
    AVG(latency_ms) AS avg_latency_ms,
    AVG(completion_tokens)::DOUBLE PRECISION AS avg_completion_tokens
FROM llm_events
WHERE task_type IS NOT NULL
GROUP BY bucket, task_type, model
WITH NO DATA;

SELECT add_continuous_aggregate_policy('hourly_task_stats',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Daily summary
CREATE MATERIALIZED VIEW daily_summary
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    provider,
    model,
    task_type,
    org_id,
    COUNT(*) AS request_count,
    SUM(estimated_cost) AS total_cost,
    AVG(latency_ms) AS avg_latency_ms,
    SUM(total_tokens) AS total_tokens,
    COUNT(*) FILTER (WHERE status = 'failure') AS failure_count,
    COUNT(*) FILTER (WHERE has_tool_calls) AS tool_call_count
FROM llm_events
GROUP BY bucket, provider, model, task_type, org_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('daily_summary',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day');

-- Compression: compress chunks older than 7 days
SELECT add_compression_policy('llm_events', INTERVAL '7 days');
SELECT add_compression_policy('tool_calls', INTERVAL '7 days');

-- Retention: drop raw data after 90 days (aggregates remain)
SELECT add_retention_policy('llm_events', INTERVAL '90 days');
SELECT add_retention_policy('tool_calls', INTERVAL '90 days');
