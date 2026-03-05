# 0C — CLI + Dashboard MVP

**Status:** done
**Owner:** fe
**Target:** Weeks 2–6
**Dependencies:** 0A (schema), 0B (waste score)
**Blocks:** 1E

## Objective

Two user-facing interfaces: a CLI for quick stats and a React dashboard for visual exploration.

## Tasks

- [x] **0C-1** `blockthrough stats` CLI — daily/weekly summary — `fe` (done 2026-03-03)
- [x] **0C-2** Waste score calculation — real implementation with model cost tiers — `fe` (done 2026-03-03)
- [x] **0C-3** React SPA dashboard — time-series charts (Recharts) — `fe` (done 2026-03-03)
- [x] **0C-4** API layer between dashboard/CLI and PostgreSQL (REST via FastAPI) — `fe` (done 2026-03-03)

## Technical Notes

- Waste score: 3 cost tiers, 6 models, task-type-specific downgrade rules
- 23 unit tests for waste scoring logic
- Dashboard: dark theme, 4 charts, StatsBar, TopTracesTable, shared CardShell
- CLI `blockthrough evaluate` runs classifier accuracy report

## Blockers

_None_
