# 2B — Vendor Accountability Reports

**Status:** not started
**Owner:** ml + web3
**Target:** Weeks 14–18
**Dependencies:** 1A (benchmarking engine), 2A (attestation protocol)
**Blocks:** none

## Objective

Detect silent model updates from LLM providers and generate on-chain-anchored proof of performance changes. Gives orgs immutable evidence for vendor negotiations.

## Tasks

- [ ] **2B-1** Continuous benchmark monitoring — detect model performance drift over time (statistical significance testing) — `ml`
- [ ] **2B-2** Vendor accountability report generation (model, task type, performance delta, time range, confidence interval) — `ml`
- [ ] **2B-3** On-chain anchoring of drift reports via 2A attestation layer — `web3`
- [ ] **2B-4** Dashboard integration — "Model Change Detected" alerts with drill-down — `fe`

## Technical Notes

- Drift detection: compare rolling 7-day benchmark scores against 30-day baseline, flag >5% degradation at p<0.05
- Reports should include: before/after quality scores, sample size, affected task types, estimated cost impact
- This happens more often than people think — Anthropic and OpenAI regularly update models without versioning

## Blockers

_None_
