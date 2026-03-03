# 2A — On-Chain Attestation Protocol

**Status:** done
**Owner:** web3
**Target:** Weeks 1–12
**Dependencies:** none
**Blocks:** 2B, 2C, 2D, 2E, 3A, 3C, 3D

## Objective

Design and deploy the on-chain attestation layer. Cryptographic proofs of AI operations go on-chain; actual data stays off-chain.

## Tasks

- [x] **2A-1** Design attestation schema (org ID pseudonymous, metrics hash, benchmark hash, Merkle root) — `web3` (done 2026-03-03)
- [x] **2A-2** Evaluate EAS vs custom smart contracts — decided custom for batch attestation — `web3` (done 2026-03-03)
- [x] **2A-3** L2 chain selection — Base recommended, Optimism fallback — `web3` (done 2026-03-03)
- [x] **2A-4** Smart contract development (Foundry project, deploy to Base Sepolia) — `web3` (done 2026-03-03)
- [x] **2A-5** Off-chain ↔ on-chain bridge (Merkle tree construction, proof generation) — `web3` (done 2026-03-03)
- [x] **2A-6** Chain-agnostic abstraction layer (EVMProvider, LocalProvider) — `web3` (done 2026-03-03)

## Technical Notes

- ADR-004 at `.tracker/decisions/ADR-004-attestation-protocol.md` (580 lines)
- Custom contract chosen over EAS: batch attestation saves 61% gas, per-org chain linkage enforced at contract level
- Base L2: strongest attestation ecosystem, OP Stack, native Foundry support
- 242 bytes on-chain per attestation, $0.0014 per single attest
- Batch of 100 orgs: $0.054/day ($1.62/month)
- Dual-hash: SHA-256 off-chain (Phase 0 hasher), Keccak-256 on-chain (native EVM)
- Implementation: 12 weeks (Alpha W1-3, Beta W4-6, Gamma W7-10, Audit W8-10, Mainnet W11-12)
- LocalProvider first so downstream work doesn't need real chain

## Blockers

_None_
