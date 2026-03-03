# 2A — On-Chain Attestation Protocol

**Status:** not started
**Owner:** web3
**Target:** Weeks 1–12 (design starts early, implementation follows)
**Dependencies:** none
**Blocks:** 2B, 2C, 2D, 2E, 3A, 3C, 3D

## Objective

Design and deploy the on-chain attestation layer. Cryptographic proofs of AI operations go on-chain; actual data stays off-chain. Privacy-preserving, low-cost, chain-agnostic.

## Tasks

- [ ] **2A-1** Design attestation schema (org ID pseudonymous, time period, metrics hash, benchmark hash, Merkle root of trace evaluations) — `web3`
- [ ] **2A-2** Evaluate EAS (Ethereum Attestation Service) vs custom smart contracts — write comparison doc — `web3`
- [ ] **2A-3** L2 chain selection — compare Base, Arbitrum, Optimism on gas cost, tooling, ecosystem, bridge availability — `web3`
- [ ] **2A-4** Smart contract development (attestation registry + verification functions) — `web3`
- [ ] **2A-5** Off-chain ↔ on-chain bridge: Merkle tree construction from trace evaluations, proof generation, verification — `web3`
- [ ] **2A-6** Chain-agnostic abstraction layer (interface that allows swapping L2s without upstream changes) — `web3`

## Technical Notes

- Attestation is a lightweight record: ~200 bytes on-chain per attestation (hashes only)
- Merkle tree: leaves = individual trace evaluation hashes, root = single on-chain anchor
- EAS may be simpler and cheaper than custom contracts — strong preference unless it lacks needed flexibility
- Gas budget estimate: ~$0.01–0.05 per attestation on L2, batched daily
- Design for batch attestation: one on-chain tx per org per day, not per request

## Blockers

_None_
