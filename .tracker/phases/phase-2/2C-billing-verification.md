# 2C — Billing Verification

**Status:** not started
**Owner:** be1 + web3
**Target:** Weeks 14–18
**Dependencies:** 0A (token counting), 2A (attestation)
**Blocks:** none

## Objective

Cross-reference independently counted token usage against provider invoices. Generate on-chain billing attestations. For orgs spending $50K+/month, even 2% discrepancy is material.

## Tasks

- [ ] **2C-1** Provider invoice parsing — Anthropic billing API, OpenAI usage API, Google Cloud billing export — `be1`
- [ ] **2C-2** Token count reconciliation engine — AgentProof observed tokens vs provider billed tokens — `be1`
- [ ] **2C-3** Billing attestation generation + on-chain anchoring (observed X, billed Y, delta Z) — `web3`
- [ ] **2C-4** Discrepancy alerting and monthly billing report — `be1`

## Technical Notes

- Token counting discrepancies can arise from: tokenizer differences, system prompt injection by providers, retry billing
- Need to account for LiteLLM's token counting potentially differing from provider's count — track both
- Billing attestation is high-value for enterprise sales — "we independently verified your AI spend"

## Blockers

_None_
