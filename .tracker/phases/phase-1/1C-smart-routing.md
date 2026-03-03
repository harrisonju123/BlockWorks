# 1C — Smart Routing Engine

**Status:** done
**Owner:** be2 + infra
**Target:** Weeks 10–14
**Dependencies:** 1A (fitness matrix)
**Blocks:** 1E (budget-based routing)

## Objective

Extend LiteLLM's router with a policy engine that uses the fitness matrix for real-time model selection.

## Tasks

- [x] **1C-1** Design routing policy DSL/config format (YAML) — `be2` (done 2026-03-03)
- [x] **1C-2** Extend LiteLLM router with AgentProof policy engine hook — `be2` (done 2026-03-03)
- [x] **1C-3** Real-time routing decisions using fitness matrix lookups (<2ms) — `be2` (done 2026-03-03)
- [x] **1C-4** Policy validation and dry-run mode — `be2` (done 2026-03-03)
- [x] **1C-5** A/B testing framework — `infra` (done 2026-03-03)

## Technical Notes

- `src/agentproof/routing/` package: types, policy DSL, router, dry-run, A/B testing
- YAML policy format with task_type → model selection criteria
- Quality floor (0.7) enforced as safety net regardless of policy
- FitnessCache with configurable TTL for <2ms decisions
- Deterministic A/B assignment via SHA-256 hash of trace_id
- 6 API endpoints: policy CRUD, dry-run, decisions, A/B config/results
- 56 unit tests

## Blockers

_None_
