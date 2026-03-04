-- ============================================================
-- Phase 1: Benchmark Config Persistence
-- Replaces in-memory _runtime_config with a DB-backed singleton.
-- Regular table (not hypertable) — this is config state, not time-series.
-- ============================================================

CREATE TABLE IF NOT EXISTS benchmark_config (
    id                  TEXT PRIMARY KEY DEFAULT 'default',
    enabled             BOOLEAN NOT NULL DEFAULT FALSE,
    sample_rate         DOUBLE PRECISION NOT NULL DEFAULT 0.05
                        CHECK (sample_rate >= 0.0 AND sample_rate <= 1.0),
    benchmark_models    TEXT[] NOT NULL DEFAULT ARRAY['claude-haiku-4-5-20251001', 'gpt-4o-mini'],
    judge_model         TEXT NOT NULL DEFAULT 'claude-haiku-4-5-20251001',
    enabled_task_types  TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the singleton row so GET always returns something,
-- even before the first POST /config call.
INSERT INTO benchmark_config (id) VALUES ('default')
ON CONFLICT (id) DO NOTHING;
