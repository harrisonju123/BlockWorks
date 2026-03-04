-- ============================================================
-- Phase 1C: Routing Policy & Decision Persistence
-- Separate file from schema.sql per ADR-003 guidelines.
-- Applied as a migration after the base schema exists.
-- ============================================================

-- Routing policies: versioned policy snapshots.
-- Regular table (not hypertable) — low-cardinality config data
-- queried by is_active flag, not by time range.
CREATE TABLE routing_policies (
    id              UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_json     JSONB NOT NULL,
    version         INT NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one active policy at a time
CREATE UNIQUE INDEX idx_routing_policies_active
    ON routing_policies (is_active) WHERE is_active;

CREATE INDEX idx_routing_policies_version
    ON routing_policies (version DESC);


-- Routing decisions: time-series log of every routing resolution.
-- Hypertable because this grows unbounded and the dashboard needs
-- time-range queries for routing decision timelines.
CREATE TABLE routing_decisions (
    id               UUID NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL,
    task_type        TEXT,
    requested_model  TEXT NOT NULL,
    selected_model   TEXT NOT NULL,
    was_overridden   BOOLEAN NOT NULL DEFAULT FALSE,
    reason           TEXT,
    policy_version   INT,
    group_name       TEXT,

    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('routing_decisions', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_routing_decisions_task
    ON routing_decisions (task_type, created_at DESC);

CREATE INDEX idx_routing_decisions_override
    ON routing_decisions (was_overridden, created_at DESC)
    WHERE was_overridden;

-- Compression and retention
ALTER TABLE routing_decisions SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'task_type'
);
SELECT add_compression_policy('routing_decisions', INTERVAL '7 days');
SELECT add_retention_policy('routing_decisions', INTERVAL '90 days');
