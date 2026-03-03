# 1E — Alerts & Budgets

**Status:** done
**Owner:** fe + infra
**Target:** Weeks 8–12
**Dependencies:** 0A (data pipeline), 0C (dashboard)
**Blocks:** none

## Objective

Spend alerts, budget caps with automatic model downgrade, and anomaly detection.

## Tasks

- [x] **1E-1** Per-project and per-user spend tracking aggregation layer — `infra` (done 2026-03-03)
- [x] **1E-2** Slack + email alert integrations (webhook-based) — `fe` (done 2026-03-03)
- [x] **1E-3** Budget caps with automatic model downgrade — `infra` (done 2026-03-03)
- [x] **1E-4** Anomaly detection — Z-score on rolling 7-day baseline from daily_summary — `infra` (done 2026-03-03)
- [x] **1E-5** Intelligent thresholds — dynamic baselines via continuous aggregates — `infra` (done 2026-03-03)

## Technical Notes

- `schema_alerts.sql`: alert_rules, budget_configs, alert_history (hypertable)
- `alerts/anomaly.py`: Z-score spend anomaly (2.0=warning, 3.0=critical), model switch detection, failure rate spikes
- `alerts/budgets.py`: 80%/95%/100% thresholds with configurable action (alert/downgrade/block)
- `alerts/notify.py`: Slack Block Kit + SMTP email (via run_in_executor, non-blocking)
- `alerts/checker.py`: Background loop with cooldown deduplication, dispatch_alert wired in
- 8 API endpoints: CRUD rules, alert history, budget management
- 79 unit tests covering anomaly detection, budgets, notifications, checker lifecycle
- SMTP blocking issue fixed in simplify (run_in_executor)
- AlertChecker dispatch wiring and _last_fired pruning added in simplify

## Blockers

_None_
