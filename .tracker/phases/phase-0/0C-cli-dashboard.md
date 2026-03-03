# 0C — CLI + Dashboard MVP

**Status:** in progress
**Owner:** fe
**Target:** Weeks 2–6
**Dependencies:** 0A (schema — can stub early), 0B (waste score)
**Blocks:** 1E

## Objective

Two user-facing interfaces: a CLI for quick stats and a React dashboard for visual exploration. This is the demo artifact for early users.

## Tasks

- [x] **0C-1** `agentproof stats` CLI — daily/weekly summary: spend by provider, spend by task type, top 10 expensive traces — `fe` (done 2026-03-03)
- [ ] **0C-2** Waste score calculation (% of calls where a cheaper model likely sufficed — real implementation, not placeholder) — `fe`
- [~] **0C-3** React SPA dashboard — time-series charts (Recharts), same data as CLI — `fe` **IN PROGRESS** (scaffold + API types done, needs actual pages/charts)
- [x] **0C-4** API layer between dashboard/CLI and PostgreSQL (REST via FastAPI) — `fe` (done 2026-03-03)

## Technical Notes

- CLI reads API base URL from config with `--api-url` override
- API uses `resolve_time_range()` helper for consistent default time windows
- Events endpoint uses typed `EventDetail` response model (not raw dicts)
- Events endpoint validates `TaskType` and `EventStatus` enums
- Events endpoint uses `COUNT(*) OVER()` window function (single query instead of two)
- Dashboard scaffold: package.json, vite config, TypeScript API types, API client
- Still needs: actual React pages, chart components, layout shell

## Blockers

_None_
