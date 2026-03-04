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

## Productization — Wiring & Surfacing Existing Code

> Goal: All backend features exist but aren't visible/connected. Wire them up.

### Sprint 1: Make Existing Features Visible (DONE 2026-03-03)

| ID | Task | Status |
|----|------|--------|
| A1 | react-router-dom, sidebar Shell, BrowserRouter | done |
| A2 | Events explorer page (filterable table) | done |
| A3 | Alerts management page (rules CRUD, budget view) | done |
| A4 | Benchmarks page (fitness matrix, results, drift) | done |
| A5 | Waste details page | done |
| B1 | Fix `_MAX_RECENT_DECISIONS` undefined in routing.py | done |
| B2 | Enable feature flags in docker-compose | done |

### Sprint 2: Wire Disconnected Pieces (DONE 2026-03-03)

| ID | Task | Status |
|----|------|--------|
| C1 | Integrate `resolve()` into proxy at classification points | done |
| C2 | Background FitnessCache refresh task | done |
| C3 | Routing dashboard page (policy view, decisions feed, dry-run) | done |
| D1 | Persist alerts to DB (replace in-memory dicts) | done |
| E1 | Wire `should_sample()` into proxy + BenchmarkWorker in lifespan | done |
| E2 | Persist benchmark config to DB | done |

Simplify + code-review fixes applied:
- Dual policy/cache bug fixed (routing.py reads app.state, not module globals)
- Shutdown ordering fixed (workers drain before httpx clients close)
- Anthropic multi-text-block capture fixed (accumulate, not overwrite)
- FitnessCache eager seed on startup
- Removed redundant SELECT before UPDATE in alerts
- Frontend: offset=0 falsy fix, SortIndicator extraction, per-rule delete tracking

### Sprint 3: Hardening & Integration (DONE 2026-03-03)

| ID | Task | Status |
|----|------|--------|
| F1 | DB-backed routing policy storage | done |
| F2 | Routing decisions hypertable + writer | done |
| G1 | Proxy-to-routing E2E integration test | done |
| G2 | Alert persistence integration test | done |
| H2 | Event detail drawer/modal | done |
| H3 | MCP tracing page | done |

Simplify + code-review fixes applied:
- Added missing `GET /events/{id}` endpoint (EventDrawer was calling non-existent route)
- Rewrote `_get_active_policy` to read app.state first (no DB on every GET)
- Extracted `_load_policy_from_row` helper (dedup between lifespan and routing.py)
- Combined startup DB sessions (policy + fitness cache in single session)
- `get_routing_decisions` uses window function (single round-trip)
- Fixed decisions fallback `total > 0` semantic gap
- `datetime.now(timezone.utc)` → `utcnow()` in tests
- EventDrawer: `role="dialog"`, `aria-modal`, body scroll lock
- Waste panel keyed by composite instead of array index
- Page reset on timeRange change
- UUID validation on event detail endpoint
- Consistent null sentinel `"---"` across pages

### Sprint 4: Blockchain Integration (DONE 2026-03-04)

| ID | Task | Status |
|----|------|--------|
| I1 | Deploy all 6 contracts (Deploy.s.sol) | done |
| I2 | Anvil Docker + auto-deploy (docker-compose, deploy-local.sh) | done |
| I3 | EVMProvider implementation (web3.py async, factory auto-discovery) | done |
| I4 | Attestation dashboard page (latest attestation, chain integrity) | done |

Simplify + code-review pending.

### Sprint 5+: Advanced Features (NOT STARTED)

## Codebase Stats

| Metric | Value |
|--------|-------|
| Unit tests | 1435 passing |
| Integration tests | 20 (8 proxy-routing, 12 alerts-db) |
| Source packages | 25 (pipeline, classifier, api, cli, benchmarking, mcp, alerts, waste, routing, attestation, billing, compliance, channels, validators, governance, trust, sdk, fitness, registry, enterprise, workflows, revenue, interop) |
| Solidity contracts | 6 (Attestation, Channel, Staking, Token, Trust, Revenue) |
| API endpoints | 60+ |
| SQL schemas | 6 (schema.sql, schema_benchmarks.sql, schema_mcp.sql, schema_alerts.sql, schema_benchmark_config.sql, schema_routing.sql) |
| Dashboard pages | 9 (Overview, Events, Alerts, Benchmarks, Waste, Routing, MCP Tracing, Attestations + Event Detail drawer) |
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
