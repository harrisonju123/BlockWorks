# 1B — Waste Detection & Recommendations

**Status:** done
**Owner:** ml + fe
**Target:** Weeks 10–14
**Dependencies:** 0B (classifier), 1A (fitness matrix)
**Blocks:** none (drives adoption)

## Objective

Analyze usage patterns and flag specific waste categories with dollar amounts.

## Tasks

- [x] **1B-1** Model overkill detector — fitness matrix comparison — `ml` (done 2026-03-03)
- [x] **1B-2** Redundant call detector — hash-based duplicate detection — `ml` (done 2026-03-03)
- [x] **1B-3** Context bloat analyzer — system prompt vs completion token ratio — `ml` (done 2026-03-03)
- [x] **1B-4** Cache miss detector — prompt_hash duplicates within time window — `ml` (done 2026-03-03)
- [x] **1B-5** Agent loop detector — fix-break-fix cycle pattern matching — `ml` (done 2026-03-03)
- [x] **1B-6** Weekly waste report generation — plain text + Slack blocks — `fe` (done 2026-03-03)

## Technical Notes

- `src/agentproof/waste/` package: types, 5 detectors, analyzer, report formatter
- Each detector is a pure function (mock-testable, no DB coupling)
- Analyzer orchestrates all detectors and computes overall waste_score
- CLI: `agentproof waste-report`
- API: `GET /api/v1/stats/waste/details` (backward-compatible with existing /waste-score)
- 68 unit tests

## Blockers

_None_
