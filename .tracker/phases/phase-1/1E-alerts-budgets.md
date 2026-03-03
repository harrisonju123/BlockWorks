# 1E — Alerts & Budgets

**Status:** not started
**Owner:** fe + infra
**Target:** Weeks 8–12
**Dependencies:** 0A (data pipeline), 0C (dashboard)
**Blocks:** none

## Objective

Spend alerts, budget caps with automatic model downgrade, and anomaly detection. Prevent runaway costs from agent loops and misconfigurations.

## Tasks

- [ ] **1E-1** Per-project and per-user spend tracking aggregation layer — `infra`
- [ ] **1E-2** Slack + email alert integrations (webhook-based) — `fe`
- [ ] **1E-3** Budget caps with automatic model downgrade (integrates with 1C smart routing) — `infra`
- [ ] **1E-4** Anomaly detection — statistical baseline of spend patterns, flag deviations (Z-score or similar) — `infra`
- [ ] **1E-5** Extend LiteLLM budget tracking with intelligent thresholds (dynamic, based on rolling averages vs fixed numbers) — `infra`

## Technical Notes

- Anomaly detection should catch: infinite agent loops, sudden spike in token usage, unexpected model switches
- Budget caps: soft cap (alert) vs hard cap (downgrade model) vs kill switch (reject requests)
- Intelligent thresholds: "alert when daily spend exceeds 2x the 7-day rolling average" instead of fixed $100/day
- Slack integration: use incoming webhooks, keep it simple

## Blockers

_None_
