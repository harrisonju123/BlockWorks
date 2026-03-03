# AgentProof — Project Board

> Last updated: 2026-03-03

## Phase 0 — Foundation (COMPLETE)

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 0A | Core Data Pipeline | infra + be1 | done | — | W1–W5 |
| 0B | Task Classification Engine | ml | done | — | W1–W5 |
| 0C | CLI + Dashboard MVP | fe | done | 0A (schema) | W2–W6 |
| 0D | Initial Integrations | be2 | done | 0A (callback) | W3–W7 |

## Phase 1 — Intelligence Layer (COMPLETE)

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 1A | Cross-Provider Benchmarking | ml + be2 | done | 0A, 0B | W6–W12 |
| 1B | Waste Detection & Recommendations | ml + fe | done | 0B, 1A | W10–W14 |
| 1C | Smart Routing Engine | be2 + infra | done | 1A | W10–W14 |
| 1D | MCP Server Tracing | be1 | done | 0A | W6–W10 |
| 1E | Alerts & Budgets | fe + infra | done | 0A, 0C | W8–W12 |

### Hardening (completed before Phase 1 features)
- [x] EventWriter graceful shutdown (drain queue on SIGTERM)
- [x] Queries wired to continuous aggregates (daily_summary, hourly_model_stats)
- [x] pg_interval parameterized in SQL (CAST(:bucket_interval AS INTERVAL))

## Phase 2 — Attestation Layer (COMPLETE)

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 2A | On-Chain Attestation Protocol | web3 | done | — | W1–W12 |
| 2B | Vendor Accountability Reports | ml + web3 | done | 1A, 2A | W14–W18 |
| 2C | Billing Verification | be1 + web3 | done | 0A, 2A | W14–W18 |
| 2D | Compliance Audit Trail | be1 + infra | done | 0A, 2A | W14–W20 |
| 2E | State Channel Foundation | web3 | done | 2A | W18–W22 |

## Phase 3 — Protocol & Network (COMPLETE)

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 3A | Decentralized Validation | web3 + be2 | done | 2A, 1A | W20–W26 |
| 3B | Global Model Fitness Index | ml + fe | done | 1A, 3A | W24–W30 |
| 3C | Token Design & Launch | web3 | done | 2A | W22–W30 |
| 3D | Agent Trust Scores | ml + be2 | done | 1A, 2A | W24–W30 |
| 3E | SDK Ecosystem Expansion | be1 + fe | done | 0A | W20–W28 |

## Phase 4 — Marketplace (COMPLETE)

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 4A | Agent & MCP Registry | be2 + fe | done | 3D | W28+ |
| 4B | Composable Workflow Builder | be1 + fe | done | 4A | W30+ |
| 4C | Revenue Sharing Protocol | web3 | done | 2E, 4A | W32+ |
| 4D | Enterprise Multi-Tenant | infra + be1 | done | 2D | W30+ |
| 4E | Cross-Platform Interop | be2 + web3 | done | 4A, 4C | W36+ |

## Codebase Stats

| Metric | Value |
|--------|-------|
| Unit tests | 1382 passing |
| Source packages | 25 (pipeline, classifier, api, cli, benchmarking, mcp, alerts, waste, routing, attestation, billing, compliance, channels, validators, governance, trust, sdk, fitness, registry, enterprise, workflows, revenue, interop) |
| Solidity contracts | 6 (Attestation, Channel, Staking, Token, Trust, Revenue) |
| API endpoints | 60+ |
| SQL schemas | 4 (schema.sql, schema_benchmarks.sql, schema_mcp.sql, schema_alerts.sql) |
| Integration guides | 4 (Claude Code, OpenCode, LangChain, CrewAI) |
| ADRs | 4 (Phase 0 arch, Phase 1 arch, Attestation protocol) |

## Architecture Decisions

- [ADR-001](decisions/ADR-001-phase0-architecture.md) — Phase 0 architecture, tech stack, interface contracts
- [ADR-003](decisions/ADR-003-phase1-architecture.md) — Phase 1 architecture, new schemas, parallel execution plan

## Critical Path

```
DONE:  0A → 0B → 1A → 1B + 1C (full intelligence layer)
DONE:  0A → 0C → 1E (alerts & budgets)
DONE:  0A → 1D (MCP tracing)
DONE:  2A → 2B/2C/2D (parallel) → 2E (state channels)
DONE:  3A/3B/3C/3D/3E (protocol & network)
DONE:  4A/4B/4C/4D/4E (marketplace)
DONE:  Tech debt backlog (7 items)
```

## Technical Debt / Simplify Backlog

Completed (2026-03-03):

- [x] Extract `AsyncQueueWorker` base class — `pipeline/base_worker.py`, EventWriter/MCPWriter/BenchmarkWorker refactored
- [x] Consolidate `MCPCallStatus` into `EventStatus` — alias removed, all refs updated
- [x] Consolidate `MODEL_DOWNGRADE_MAP` with `MODEL_COST_TIERS` — unified into `models.py` `MODEL_CATALOG`
- [x] Type `BenchmarkResult.task_type` as `TaskType` enum
- [x] MCP response schema dedup — `MCPServerStatsItem` removed, uses canonical `MCPServerStats`
- [x] Add `utcnow()` helper — `utils.py`, applied to 29 files (53 call sites)
- [x] Fix keyword substring false positives — word-boundary regex for single-word keywords

Remaining:

- [ ] `BenchmarkWorker._flush` raises `NotImplementedError` — consider splitting `AsyncPoolWorker` / `AsyncQueueWorker`
- [ ] `MCPWriter._call_queue` duplicates `self._queue` from base class
