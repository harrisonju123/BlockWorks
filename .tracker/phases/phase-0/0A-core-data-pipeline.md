# 0A — Core Data Pipeline

**Status:** in progress
**Owner:** infra + be1
**Target:** Weeks 1–5
**Dependencies:** none
**Blocks:** 0C, 0D, 1A, 1D, 1E, 2C, 2D

## Objective

Stand up the foundational data capture layer: LiteLLM callback plugin + PostgreSQL/TimescaleDB storage. Every subsequent feature reads from this pipeline.

## Tasks

- [x] **0A-1** Set up PostgreSQL + TimescaleDB. Define schema for raw events (model, tokens in/out, latency, cost, trace context, content SHA-256 hash) — `infra` (done 2026-03-03)
- [x] **0A-2** Build LiteLLM custom callback handler using `log_success_event` / `log_failure_event` async hooks — `be1` (done 2026-03-03)
- [x] **0A-3** SHA-256 hashing layer for prompt content before storage (never store raw user data) — `be1` (done 2026-03-03)
- [x] **0A-4** Session/trace context propagation (session ID, parent span, agent framework detection) — `be1` (done 2026-03-03)
- [ ] **0A-5** Integration tests — verify zero-latency-impact logging under load (target: <8ms P95 overhead) — `infra`
- [x] **0A-6** Docker Compose local dev environment (LiteLLM proxy + Postgres + TimescaleDB) — `infra` (done 2026-03-03)

## Technical Notes

- Use LiteLLM's built-in cost calculator for estimated cost per call
- TimescaleDB hypertable on `created_at` column for efficient time-series queries
- Schema should capture tool calls (function name, args hash) when present
- Callback must be fully async — never block the request/response path
- Writer uses COPY protocol for batch throughput (5-10x vs executemany)
- asyncio.Lock guards lazy writer initialization for concurrency safety
- Retry logic with individual-insert fallback on batch failures

## Blockers

_None_
