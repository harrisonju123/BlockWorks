# 1C — Smart Routing Engine

**Status:** not started
**Owner:** be2 + infra
**Target:** Weeks 10–14
**Dependencies:** 1A (fitness matrix)
**Blocks:** 1E (budget-based routing)

## Objective

Extend LiteLLM's router with a policy engine that uses the fitness matrix to make real-time model selection decisions. This is the feature that directly saves money.

## Tasks

- [ ] **1C-1** Design routing policy DSL/config format (YAML rules: task type → model selection criteria) — `be2`
- [ ] **1C-2** Extend LiteLLM router with AgentProof policy engine hook — `be2`
- [ ] **1C-3** Real-time routing decisions using fitness matrix lookups (<2ms decision latency) — `be2`
- [ ] **1C-4** Policy validation and dry-run mode ("what would have happened last week under this policy") — `be2`
- [ ] **1C-5** A/B testing framework — route X% to policy, (100-X)% to default, compare outcomes — `infra`

## Technical Notes

- Policy example: `{task: "classification", criteria: "cheapest where quality > 0.9", fallback: "sonnet"}`
- Dry-run mode queries historical data and simulates routing decisions — critical for user trust
- Must integrate with LiteLLM's existing fallback/retry logic, not replace it
- A/B test results feed back into the fitness matrix

## Blockers

_None_
