-- Feedback signals for routing quality adjustment
CREATE TABLE IF NOT EXISTS feedback_signals (
    id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_id UUID NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('retry', 'override', 'abandon', 'explicit_positive', 'explicit_negative')),
    quality_delta DOUBLE PRECISION NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'implicit',
    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('feedback_signals', 'created_at',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Dedup: same event + signal type
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_event_signal
    ON feedback_signals (event_id, signal, created_at);

-- Aggregation queries: model + task_type over time
CREATE INDEX IF NOT EXISTS idx_feedback_model_task
    ON feedback_signals (model, task_type, created_at DESC);

-- Compression and retention
SELECT add_compression_policy('feedback_signals', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('feedback_signals', INTERVAL '90 days', if_not_exists => TRUE);

-- Index on llm_events for the feedback detector's LAG window queries
-- Covers partition by (session_id, prompt_hash) with created_at ordering
CREATE INDEX IF NOT EXISTS idx_llm_events_session_prompt
    ON llm_events (session_id, prompt_hash, created_at DESC)
    WHERE session_id IS NOT NULL AND prompt_hash IS NOT NULL;
