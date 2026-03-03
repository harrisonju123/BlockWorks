# AgentProof — Project Board

> Last updated: 2026-03-03

## Phase 0 — Foundation

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 0A | Core Data Pipeline | infra + be1 | in progress | — | W1–W5 |
| 0B | Task Classification Engine | ml | in progress | — | W1–W5 |
| 0C | CLI + Dashboard MVP | fe | in progress | 0A (schema) | W2–W6 |
| 0D | Initial Integrations | be2 | not started | 0A (callback) | W3–W7 |

## Phase 1 — Intelligence Layer

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 1A | Cross-Provider Benchmarking | ml + be2 | not started | 0A, 0B | W6–W12 |
| 1B | Waste Detection & Recommendations | ml + fe | not started | 0B, 1A | W10–W14 |
| 1C | Smart Routing Engine | be2 + infra | not started | 1A | W10–W14 |
| 1D | MCP Server Tracing | be1 | not started | 0A | W6–W10 |
| 1E | Alerts & Budgets | fe + infra | not started | 0A, 0C | W8–W12 |

## Phase 2 — Attestation Layer

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 2A | On-Chain Attestation Protocol | web3 | not started | — | W1–W12 |
| 2B | Vendor Accountability Reports | ml + web3 | not started | 1A, 2A | W14–W18 |
| 2C | Billing Verification | be1 + web3 | not started | 0A, 2A | W14–W18 |
| 2D | Compliance Audit Trail | be1 + infra | not started | 0A, 2A | W14–W20 |
| 2E | State Channel Foundation | web3 | not started | 2A | W18–W22 |

## Phase 3 — Protocol & Network

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 3A | Decentralized Validation | web3 + be2 | not started | 2A, 1A | W20–W26 |
| 3B | Global Model Fitness Index | ml + fe | not started | 1A, 3A | W24–W30 |
| 3C | Token Design & Launch | web3 | not started | 2A | W22–W30 |
| 3D | Agent Trust Scores | ml + be2 | not started | 1A, 2A | W24–W30 |
| 3E | SDK Ecosystem Expansion | be1 + fe | not started | 0A | W20–W28 |

## Phase 4 — Marketplace (Placeholder)

| ID | Initiative | Owner | Status | Dependencies | Target |
|----|-----------|-------|--------|--------------|--------|
| 4A | Agent & MCP Registry | TBD | not started | 3D | W28+ |
| 4B | Composable Workflow Builder | TBD | not started | 4A | W30+ |
| 4C | Revenue Sharing Protocol | TBD | not started | 2E, 4A | W32+ |
| 4D | Enterprise Multi-Tenant | TBD | not started | 2D | W30+ |
| 4E | Cross-Platform Interop | TBD | not started | 4A, 4C | W36+ |

## Team Allocation

| Role | Person | Current Focus |
|------|--------|---------------|
| Infra Lead | TBD | — |
| Backend 1 | TBD | — |
| Backend 2 | TBD | — |
| Frontend/CLI | TBD | — |
| ML/Eval | TBD | — |
| Web3/Protocol | TBD | — |

## Critical Path

```
0A → 0B → 1A → 1B/1C (core value prop)
0A → 0C → 1E (user-facing observability)
0A → 1D (MCP tracing — unique differentiator)
2A → 2B/2C/2D → 2E (attestation stack)
```
