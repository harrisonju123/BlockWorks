-- ============================================================
-- Phase 1: Benchmark Results Schema
-- Applied as a migration alongside the existing schema.sql.
-- Does NOT modify the existing llm_events or tool_calls tables.
-- ============================================================

-- Benchmark results: one row per (original_event, benchmark_model, judge_run).
-- Tracks how an alternative model performed on the same prompt,
-- scored by an LLM-as-judge against a task-specific rubric.
CREATE TABLE benchmark_results (
    id                  UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    original_event_id   UUID NOT NULL,
    original_model      TEXT NOT NULL,
    benchmark_model     TEXT NOT NULL,
    task_type           TEXT NOT NULL,
    quality_score       DOUBLE PRECISION NOT NULL CHECK (quality_score BETWEEN 0.0 AND 1.0),
    original_cost       DOUBLE PRECISION NOT NULL,
    benchmark_cost      DOUBLE PRECISION NOT NULL,
    original_latency_ms DOUBLE PRECISION NOT NULL,
    benchmark_latency_ms DOUBLE PRECISION NOT NULL,
    judge_model         TEXT NOT NULL DEFAULT 'claude-haiku-4-5-20251001',
    rubric_version      TEXT NOT NULL,
    org_id              TEXT,

    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('benchmark_results', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_bench_model_task ON benchmark_results (benchmark_model, task_type, created_at DESC);
CREATE INDEX idx_bench_original ON benchmark_results (original_event_id, created_at DESC);
CREATE INDEX idx_bench_org ON benchmark_results (org_id, created_at DESC)
    WHERE org_id IS NOT NULL;

-- Continuous aggregate: fitness matrix (model x task_type -> avg scores).
-- The routing engine and waste scorer query this to pick the best model
-- for a given task type under cost/quality/latency constraints.
CREATE MATERIALIZED VIEW fitness_matrix
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at)    AS bucket,
    benchmark_model                     AS model,
    task_type,
    AVG(quality_score)                  AS avg_quality,
    AVG(benchmark_cost)                 AS avg_cost,
    AVG(benchmark_latency_ms)           AS avg_latency,
    COUNT(*)                            AS sample_size
FROM benchmark_results
GROUP BY bucket, benchmark_model, task_type
WITH NO DATA;

SELECT add_continuous_aggregate_policy('fitness_matrix',
    start_offset  => INTERVAL '3 days',
    end_offset    => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day');

-- Compression: compress chunks older than 7 days
SELECT add_compression_policy('benchmark_results', INTERVAL '7 days');

-- Retention: keep 180 days of benchmark history (long-term strategic value)
SELECT add_retention_policy('benchmark_results', INTERVAL '180 days');
