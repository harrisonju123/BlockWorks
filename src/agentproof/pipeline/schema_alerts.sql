-- ============================================================
-- Phase 1E: Alerts & Budgets Schema
-- Separate file from schema.sql per ADR-003 guidelines.
-- Applied as a migration after the base schema exists.
-- ============================================================

-- Alert rules: user-defined trigger configurations.
-- Regular table (not a hypertable) because this is low-cardinality
-- configuration data queried by org_id, not time-range scanned.
CREATE TABLE alert_rules (
    id              UUID NOT NULL PRIMARY KEY,
    org_id          TEXT NOT NULL,
    rule_type       TEXT NOT NULL CHECK (rule_type IN (
        'spend_threshold', 'anomaly_zscore', 'error_rate', 'latency_p95'
    )),
    threshold_config JSONB NOT NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('slack', 'email', 'both')),
    webhook_url     TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_alert_rules_org ON alert_rules (org_id) WHERE enabled;


-- Budget configurations: spend caps with enforcement actions.
-- Regular table for the same reason as alert_rules.
CREATE TABLE budget_configs (
    id              UUID NOT NULL PRIMARY KEY,
    org_id          TEXT NOT NULL,
    project_id      TEXT,
    budget_usd      DOUBLE PRECISION NOT NULL,
    period          TEXT NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
    action          TEXT NOT NULL CHECK (action IN ('alert', 'downgrade', 'block')),
    current_spend   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    period_start    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (org_id, project_id, period)
);

CREATE INDEX idx_budget_org ON budget_configs (org_id);


-- Alert history: fired alert records.
-- Hypertable on triggered_at because this grows unbounded and
-- the dashboard needs time-range queries for alert timelines.
CREATE TABLE alert_history (
    id              UUID NOT NULL,
    rule_id         UUID NOT NULL,
    triggered_at    TIMESTAMPTZ NOT NULL,
    message         TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ,

    PRIMARY KEY (id, triggered_at)
);

SELECT create_hypertable('alert_history', 'triggered_at',
    chunk_time_interval => INTERVAL '7 days');

CREATE INDEX idx_alert_hist_rule ON alert_history (rule_id, triggered_at DESC);
CREATE INDEX idx_alert_hist_open ON alert_history (resolved_at)
    WHERE resolved_at IS NULL;

-- Compression and retention
SELECT add_compression_policy('alert_history', INTERVAL '30 days');
SELECT add_retention_policy('alert_history', INTERVAL '365 days');
