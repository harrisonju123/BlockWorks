# 3D — Agent-to-Agent Trust Scores

**Status:** not started
**Owner:** ml + be2
**Target:** Weeks 24–30
**Dependencies:** 1A (benchmarking), 2A (attestation)
**Blocks:** 4A (registry)

## Objective

On-chain reputation system for agents and MCP servers. Trust scores based on reliability, efficiency, quality, and usage. Foundation of the marketplace.

## Tasks

- [ ] **3D-1** Trust score model — define weights for reliability (uptime, error rate), efficiency (cost per outcome), quality (eval scores), usage (call volume) — `ml`
- [ ] **3D-2** On-chain trust score registry (store scores, update history, query interface) — `web3`
- [ ] **3D-3** Trust score query API for agent-to-agent routing decisions — `be2`
- [ ] **3D-4** Score decay and update mechanics (recent data weighted higher, inactive agents decay toward neutral) — `ml`

## Blockers

_None_
