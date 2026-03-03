# ADR-001 — Phase 0 Architecture Plan

**Date:** 2026-03-03
**Status:** Proposed
**Authors:** Principal Architect
**Scope:** Full Phase 0 architecture, interface contracts, parallel execution plan, and Phase 1 handoff criteria

---

## Context

AgentProof is an AI agent observability, benchmarking, and attestation platform built on LiteLLM. Phase 0 establishes the foundation: a data pipeline that captures every LLM call, a classifier that labels each call by task type, a CLI and dashboard for visibility, and integration validation with major agent frameworks. Six engineers will work in parallel across four initiatives (0A, 0B, 0C, 0D). This document defines the architecture such that all six can begin work without further clarification.

---

## 1. Repository and Project Structure

### Decision: Monorepo

A single repository using Python namespace packages for backend components and a dedicated `dashboard/` directory for the React frontend. Rationale:

- Phase 0 components share schemas, types, and test fixtures. A monorepo eliminates version drift during rapid early development.
- Atomic commits across pipeline + classifier + API layer prevent integration skew.
- When Phase 2 introduces Solidity contracts, they live in `contracts/` within the same repo.

### Language Choices

| Component | Language | Rationale |
|-----------|----------|-----------|
| Data pipeline, callback handler, classifier, API | Python 3.12 | LiteLLM is Python. The callback hooks, async event processing, and ML classifier all belong in the same runtime. |
| CLI | Python 3.12 (Typer) | Shares models and DB access with the pipeline. Avoids a second build toolchain. |
| Dashboard | TypeScript (React 19 + Vite 6) | Standard for data-rich UIs. Separate dev server, communicates via REST API. |
| Smart contracts (Phase 2) | Solidity | Industry standard for EVM L2 deployment. |

### Directory Structure

```
agentproof/
  pyproject.toml                    # Root project config (hatch build system)
  docker-compose.yml                # Local dev stack
  docker-compose.ci.yml             # CI-specific overrides
  Dockerfile                        # API + pipeline image
  .env.example                      # Template for local env vars
  .github/
    workflows/
      ci.yml                        # Lint, test, type-check on PR
      integration.yml               # Integration tests (Docker-based)

  src/
    agentproof/
      __init__.py
      py.typed                      # PEP 561 marker

      # --- Core data pipeline (0A) ---
      pipeline/
        __init__.py
        callback.py                 # LiteLLM custom callback handler
        hasher.py                   # SHA-256 content hashing
        context.py                  # Session/trace context propagation
        models.py                   # SQLAlchemy/dataclass models for events
        schema.sql                  # Raw SQL for TimescaleDB setup
        migrations/                 # Alembic migrations
          env.py
          versions/

      # --- Task classifier (0B) ---
      classifier/
        __init__.py
        taxonomy.py                 # Task type enum and definitions
        rules.py                    # Rules-based heuristic classifier
        ml_model.py                 # Optional DistilBERT classifier
        evaluator.py                # Accuracy evaluation harness
        fixtures/                   # Synthetic training/eval data
          synthetic_prompts.jsonl

      # --- API layer (0C-4) ---
      api/
        __init__.py
        app.py                      # FastAPI application
        routes/
          __init__.py
          stats.py                  # /stats endpoints
          events.py                 # /events endpoints
          health.py                 # /health endpoint
        middleware.py               # CORS, request ID, timing
        schemas.py                  # Pydantic request/response models
        deps.py                     # Dependency injection (DB sessions, etc.)

      # --- CLI (0C-1, 0C-2) ---
      cli/
        __init__.py
        main.py                     # Typer app entry point
        commands/
          stats.py                  # agentproof stats
          config.py                 # agentproof config

      # --- Shared ---
      db/
        __init__.py
        engine.py                   # Async SQLAlchemy engine setup
        session.py                  # Session factory
        queries.py                  # Reusable query builders

      config.py                     # Pydantic Settings for env-based config
      types.py                      # Shared type definitions (TypedDicts, enums)

  tests/
    conftest.py                     # Shared fixtures (DB, event factories)
    unit/
      pipeline/
      classifier/
      api/
      cli/
    integration/
      test_callback_e2e.py          # Full callback → DB → query round trip
      test_overhead.py              # Latency overhead benchmarks
      frameworks/                   # Per-framework integration tests (0D)
        test_langchain.py
        test_crewai.py
        conftest.py

  dashboard/                        # React frontend (0C-3)
    package.json
    tsconfig.json
    vite.config.ts
    src/
      App.tsx
      api/
        client.ts                   # API client (fetch wrapper)
        types.ts                    # TypeScript types matching API schemas
      components/
        charts/
          SpendOverTime.tsx
          RequestsOverTime.tsx
          CostDistribution.tsx
          WasteScore.tsx
        layout/
          Shell.tsx
          Sidebar.tsx
      pages/
        Dashboard.tsx
      hooks/
        useStats.ts
        useEvents.ts

  docs/
    integrations/                   # Integration guides (0D)
      claude-code.md
      opencode.md
      langchain.md
      crewai.md

  .tracker/                         # Project tracking (already exists)
```

### Build Toolchain

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12.x | Runtime |
| hatch | 1.12+ | Python build system, virtual env management |
| Alembic | 1.14+ | Database migrations |
| Ruff | 0.9+ | Linting + formatting (replaces Black, isort, flake8) |
| mypy | 1.14+ | Type checking |
| pytest | 8.3+ | Testing |
| Node.js | 22 LTS | Dashboard build |
| pnpm | 9.x | Package manager for dashboard |

---

## 2. Phase 0 Parallel Execution Plan

### Dependency Graph

```
Day 1 (no deps):     0A-1, 0A-2, 0A-6, 0B-1, 0B-2, 0B-4
                      |         |         |
Week 1-2:            0A-3  ←── 0A-2      0B-3 (eval ML vs rules)
                      |
Week 2:              0A-4  ←── 0A-2 + 0A-3
                      |
Week 2 (stub data):  0C-1, 0C-3, 0C-4 (can start with mocked API)
                      |
Week 3:              0A-5  ←── 0A-1 + 0A-2 + 0A-4 + 0A-6
                     0D-1..4 ←── 0A-2 (callback handler working)
                      |
Week 4:              0B-5  ←── 0A-2 + 0B-2/0B-3 (classifier + callback integration)
                     0C real data ←── 0A pipeline working
                      |
Week 5:              0D-5  ←── 0D-1..4
                     0C-2  ←── 0B-5 (waste score needs classifier)
```

### Team Assignments and Sprint Plan

**Sprint 1 (Weeks 1-2): Foundation**

| Person | Week 1 | Week 2 |
|--------|--------|--------|
| infra | 0A-1: TimescaleDB schema + setup | 0A-6: Docker Compose stack |
| be1 | 0A-2: LiteLLM callback handler | 0A-3: SHA-256 hashing layer |
| be2 | Review 0D framework docs, set up test harnesses | 0A-4: Session/trace context (pairs with be1) |
| fe | 0C-4: FastAPI skeleton + mock data | 0C-1: CLI with stub data, 0C-3: Dashboard scaffold |
| ml | 0B-1: Define taxonomy, 0B-4: Build synthetic dataset | 0B-2: Rules-based classifier |
| web3 | 2A-1: Attestation schema design (Phase 2 headstart) | 2A-2: EAS vs custom evaluation |

**Sprint 2 (Weeks 3-4): Integration**

| Person | Week 3 | Week 4 |
|--------|--------|--------|
| infra | 0A-5: Load testing + overhead measurement | 0A-5: Tune and verify <8ms P95 |
| be1 | 0A-4: Finish context propagation | 0B-5: Integrate classifier into callback (with ml) |
| be2 | 0D-1: Claude Code integration | 0D-2: OpenCode, 0D-3: LangChain |
| fe | 0C-3: Dashboard charts (Recharts) | 0C-4: Wire API to real DB (0A pipeline landing) |
| ml | 0B-3: Evaluate DistilBERT vs rules | 0B-5: Classifier integration (with be1) |
| web3 | 2A-3: L2 chain selection | 2A-4: Start smart contract dev |

**Sprint 3 (Weeks 5-6): Polish and Handoff**

| Person | Week 5 | Week 6 |
|--------|--------|--------|
| infra | CI pipeline, Docker image builds | Phase 1 infra planning |
| be1 | Bug fixes, edge cases | 1D-1: Start MCP tracing |
| be2 | 0D-4: CrewAI, 0D-5: Automated tests | 1A-1: Start traffic mirroring |
| fe | 0C-2: Waste score (classifier available) | 1E planning, dashboard polish |
| ml | Classifier tuning, accuracy benchmarks | 1A-2: Start LLM-as-judge design |
| web3 | 2A-4: Continue smart contracts | 2A-5: Merkle tree construction |

### Critical Path

The longest sequential chain that determines Phase 0 completion:

```
0A-1 (schema) + 0A-2 (callback) → 0A-3 (hashing) → 0A-4 (context) → 0A-5 (load test)
                                                                          ↓
                                                    0B-5 (classifier integration)
                                                                          ↓
                                                    0C-2 (waste score) → Phase 0 DONE
```

Estimated critical path duration: **5 weeks**. The bottleneck is the pipeline (0A) becoming stable enough for 0B integration and 0C real data.

### What Can Start Day 1 (No Dependencies)

These tasks require zero input from other streams:

1. **0A-1** — Database schema and TimescaleDB setup
2. **0A-2** — LiteLLM callback handler (can test against mock LiteLLM)
3. **0A-6** — Docker Compose local dev environment
4. **0B-1** — Task taxonomy definition
5. **0B-2** — Rules-based classifier (operates on prompt data, no pipeline needed)
6. **0B-4** — Synthetic dataset generation
7. **0C-4** — FastAPI skeleton with mock data endpoints
8. **0C-1** — CLI skeleton with stub data
9. **0C-3** — Dashboard scaffold with mock API

### Hard Dependencies (Must Wait)

| Task | Waits For | Earliest Start |
|------|-----------|----------------|
| 0A-3 (hashing) | 0A-2 (callback structure) | Week 2 |
| 0A-4 (context) | 0A-2 (callback structure) | Week 2 |
| 0A-5 (load test) | 0A-1, 0A-2, 0A-4, 0A-6 | Week 3 |
| 0B-5 (classifier integration) | 0A-2, 0B-2 or 0B-3 | Week 4 |
| 0C-2 (waste score) | 0B-5 (needs classifier output) | Week 5 |
| 0D-1..4 (integrations) | 0A-2 (working callback) | Week 3 |
| 0D-5 (integration test suite) | 0D-1..4 | Week 5 |

---

## 3. Technology Stack Decisions

### Data Pipeline

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LiteLLM integration | Custom callback via `CustomLogger` base class | Cleanest hook: `log_success_event` and `log_failure_event` provide full request/response metadata. Async by default. |
| Async event processing | Python `asyncio` + `asyncpg` | Native async DB writes. No need for Celery/Redis at Phase 0 volume. Add a write buffer (batch inserts every 100ms or 50 events, whichever comes first) to minimize DB round trips. |
| Content hashing | SHA-256 via `hashlib` | Standard, fast, deterministic. Hash both prompt and completion content before storage. Raw content never touches disk. |
| Event serialization | Pydantic v2 models | Type-safe, fast serialization, generates JSON schema for documentation. |

### Database

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary DB | PostgreSQL 16 | Mature, excellent tooling, strong async driver support. |
| Time-series extension | TimescaleDB 2.17+ | Hypertables for automatic partitioning on `created_at`. Continuous aggregates for dashboard queries. Compression for older data. |
| Migration tool | Alembic 1.14+ | SQLAlchemy-native, supports async, well-understood. |
| Connection pooling | asyncpg (direct) at Phase 0, PgBouncer at scale | Keep it simple initially. Add PgBouncer when concurrent connections exceed 50. |

### Task Classifier

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Initial approach | Rules-based heuristic (Week 1-2 ship target) | Deterministic, zero-latency, debuggable. Ship fast, iterate with data. |
| ML evaluation | DistilBERT fine-tune (Week 3-4 evaluation) | Only adopt if it meaningfully beats rules on the synthetic eval set AND stays under 5ms P95 on CPU inference. |
| ML inference runtime | ONNX Runtime (if ML is adopted) | Fastest CPU inference for transformer models. Avoids PyTorch runtime dependency in production. |
| Confidence scoring | 0.0-1.0 float per classification | Rules-based: derive from number of matching signals. ML: use softmax probability. Flag anything below 0.7 for review. |

### CLI

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Framework | Typer 0.15+ | Type-hint-driven, auto-generates help text, built on Click. Pythonic. |
| Output formatting | Rich 13+ | Tables, progress bars, colored output. Makes `agentproof stats` output professional. |
| Configuration | `~/.agentproof/config.toml` | TOML for human readability. Store DB connection string, default time range, output format preferences. |

### Dashboard

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Framework | React 19 + Vite 6 | Fast dev server, production-ready builds, strong ecosystem. |
| Charting | Recharts 2.15+ | React-native, composable, handles time-series well. Lighter than D3 for our needs. |
| Styling | Tailwind CSS 4 | Utility-first, fast iteration, no CSS-in-JS runtime cost. |
| State management | TanStack Query v5 | Server state management, caching, background refetch. No Redux needed at this scale. |
| API communication | Plain fetch + typed client | No GraphQL needed. REST is sufficient for Phase 0 dashboard queries. |

### API Layer

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Framework | FastAPI 0.115+ | Async-native, auto-generates OpenAPI spec, Pydantic integration. |
| ASGI server | Uvicorn 0.34+ | Standard for FastAPI. Use `--workers 4` in production. |
| Serialization | orjson via Pydantic | 3-10x faster JSON serialization than stdlib json. |

### Dev Environment

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Containerization | Docker Compose v2 | Standard local dev orchestration. |
| Services in compose | LiteLLM proxy, PostgreSQL 16 + TimescaleDB, API server (hot-reload) | Minimum viable local stack. |
| Python env | hatch | Handles virtualenvs, scripts, and build. Single tool for Python project management. |
| Pre-commit hooks | Ruff (lint + format), mypy (type-check) | Fast, catches issues before CI. |

### Testing

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Unit tests | pytest 8.3+ with pytest-asyncio | Async support, fixtures, parametrize. |
| Integration tests | pytest + Docker (testcontainers-python) | Spin up real Postgres + TimescaleDB for integration tests. No mocking the DB layer. |
| Load testing | locust | Python-native, scriptable, can simulate concurrent LLM calls through the callback. |
| Coverage target | 80% for pipeline and classifier, 60% for API/CLI | Pipeline correctness is critical. UI code changes fast. |

---

## 4. Interface Contracts

These contracts are the integration boundaries. Each team codes to these interfaces. Changes require a PR review from both sides.

### 4.1 Callback Event Schema

The canonical event produced by the LiteLLM callback handler. This is the single most important interface in the system.

```python
# src/agentproof/types.py

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EventStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class TaskType(str, enum.Enum):
    """Task taxonomy (0B-1). Extensible via DB-backed registry in Phase 1."""
    CODE_GENERATION = "code_generation"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"
    EXTRACTION = "extraction"
    REASONING = "reasoning"
    CONVERSATION = "conversation"
    TOOL_SELECTION = "tool_selection"
    UNKNOWN = "unknown"


class ToolCallRecord(BaseModel):
    """Captured tool/function call within a completion."""
    tool_name: str
    args_hash: str                  # SHA-256 of serialized arguments
    response_summary_hash: str | None = None  # SHA-256 of response (if captured)


class LLMEvent(BaseModel):
    """The core event written to TimescaleDB by the callback handler."""
    id: UUID
    created_at: datetime
    status: EventStatus

    # Provider and model
    provider: str                   # e.g., "anthropic", "openai", "bedrock"
    model: str                      # e.g., "claude-opus-4", "gpt-4o"
    model_group: str | None = None  # LiteLLM model group if using router

    # Token usage
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    # Cost (USD)
    estimated_cost: float           # From LiteLLM cost calculator
    custom_pricing: float | None = None  # Override if org has custom pricing

    # Latency
    latency_ms: float               # End-to-end request duration
    time_to_first_token_ms: float | None = None  # Streaming TTFT

    # Content hashes (never store raw content)
    prompt_hash: str                # SHA-256 of full prompt (messages array serialized)
    completion_hash: str            # SHA-256 of completion content
    system_prompt_hash: str | None = None  # SHA-256 of system prompt alone

    # Trace context
    session_id: str | None = None   # Logical session grouping
    trace_id: str                   # Unique trace (may span multiple LLM calls)
    span_id: str                    # This specific call within the trace
    parent_span_id: str | None = None

    # Agent framework detection
    agent_framework: str | None = None  # "langchain", "crewai", "claude-code", etc.
    agent_name: str | None = None       # If detectable from headers/metadata

    # Tool calls
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    has_tool_calls: bool = False

    # Classification (populated by 0B, may be null initially)
    task_type: TaskType | None = None
    task_type_confidence: float | None = None

    # Error context (for failures)
    error_type: str | None = None
    error_message_hash: str | None = None

    # Metadata
    litellm_call_id: str            # LiteLLM's internal call ID
    api_base: str | None = None     # Target API endpoint
    org_id: str | None = None       # Multi-tenant org identifier
    user_id: str | None = None      # User within org (if provided via metadata)
    custom_metadata: dict | None = None  # Pass-through from litellm metadata
```

### 4.2 Database Schema

```sql
-- src/agentproof/pipeline/schema.sql

-- Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Core events table (hypertable)
CREATE TABLE llm_events (
    id              UUID PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('success', 'failure')),

    -- Provider/model
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    model_group     TEXT,

    -- Tokens
    prompt_tokens   INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens    INTEGER NOT NULL,

    -- Cost
    estimated_cost  DOUBLE PRECISION NOT NULL,
    custom_pricing  DOUBLE PRECISION,

    -- Latency
    latency_ms      DOUBLE PRECISION NOT NULL,
    time_to_first_token_ms DOUBLE PRECISION,

    -- Content hashes
    prompt_hash     TEXT NOT NULL,
    completion_hash TEXT NOT NULL,
    system_prompt_hash TEXT,

    -- Trace context
    session_id      TEXT,
    trace_id        TEXT NOT NULL,
    span_id         TEXT NOT NULL,
    parent_span_id  TEXT,

    -- Agent detection
    agent_framework TEXT,
    agent_name      TEXT,

    -- Tool calls
    has_tool_calls  BOOLEAN NOT NULL DEFAULT FALSE,

    -- Classification
    task_type       TEXT,
    task_type_confidence DOUBLE PRECISION,

    -- Error
    error_type      TEXT,
    error_message_hash TEXT,

    -- Metadata
    litellm_call_id TEXT NOT NULL,
    api_base        TEXT,
    org_id          TEXT,
    user_id         TEXT,
    custom_metadata JSONB
);

-- Convert to hypertable (partition by created_at, 1-day chunks)
SELECT create_hypertable('llm_events', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

-- Indexes for common query patterns
CREATE INDEX idx_llm_events_trace ON llm_events (trace_id, created_at DESC);
CREATE INDEX idx_llm_events_session ON llm_events (session_id, created_at DESC)
    WHERE session_id IS NOT NULL;
CREATE INDEX idx_llm_events_model ON llm_events (model, created_at DESC);
CREATE INDEX idx_llm_events_provider ON llm_events (provider, created_at DESC);
CREATE INDEX idx_llm_events_task_type ON llm_events (task_type, created_at DESC)
    WHERE task_type IS NOT NULL;
CREATE INDEX idx_llm_events_org ON llm_events (org_id, created_at DESC)
    WHERE org_id IS NOT NULL;
CREATE INDEX idx_llm_events_status ON llm_events (status, created_at DESC)
    WHERE status = 'failure';
CREATE INDEX idx_llm_events_cost ON llm_events (estimated_cost DESC, created_at DESC);

-- Tool calls (normalized, one row per tool call per event)
CREATE TABLE tool_calls (
    id                      UUID PRIMARY KEY,
    event_id                UUID NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL,
    tool_name               TEXT NOT NULL,
    args_hash               TEXT NOT NULL,
    response_summary_hash   TEXT
);

SELECT create_hypertable('tool_calls', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_tool_calls_event ON tool_calls (event_id, created_at DESC);
CREATE INDEX idx_tool_calls_name ON tool_calls (tool_name, created_at DESC);

-- Continuous aggregates for dashboard queries

-- Hourly stats by model
CREATE MATERIALIZED VIEW hourly_model_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', created_at) AS bucket,
    model,
    provider,
    COUNT(*) AS request_count,
    SUM(estimated_cost) AS total_cost,
    AVG(latency_ms) AS avg_latency_ms,
    percentile_agg(latency_ms) AS latency_pct,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    COUNT(*) FILTER (WHERE status = 'failure') AS failure_count
FROM llm_events
GROUP BY bucket, model, provider
WITH NO DATA;

SELECT add_continuous_aggregate_policy('hourly_model_stats',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Hourly stats by task type
CREATE MATERIALIZED VIEW hourly_task_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', created_at) AS bucket,
    task_type,
    model,
    COUNT(*) AS request_count,
    SUM(estimated_cost) AS total_cost,
    AVG(latency_ms) AS avg_latency_ms,
    AVG(completion_tokens) AS avg_completion_tokens
FROM llm_events
WHERE task_type IS NOT NULL
GROUP BY bucket, task_type, model
WITH NO DATA;

SELECT add_continuous_aggregate_policy('hourly_task_stats',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Daily aggregates for CLI summary
CREATE MATERIALIZED VIEW daily_summary
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    provider,
    model,
    task_type,
    org_id,
    COUNT(*) AS request_count,
    SUM(estimated_cost) AS total_cost,
    AVG(latency_ms) AS avg_latency_ms,
    SUM(total_tokens) AS total_tokens,
    COUNT(*) FILTER (WHERE status = 'failure') AS failure_count,
    COUNT(*) FILTER (WHERE has_tool_calls) AS tool_call_count
FROM llm_events
GROUP BY bucket, provider, model, task_type, org_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('daily_summary',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day');

-- Compression policy: compress chunks older than 7 days
SELECT add_compression_policy('llm_events', INTERVAL '7 days');
SELECT add_compression_policy('tool_calls', INTERVAL '7 days');

-- Retention policy: drop raw data after 90 days (aggregates remain)
SELECT add_retention_policy('llm_events', INTERVAL '90 days');
SELECT add_retention_policy('tool_calls', INTERVAL '90 days');
```

### 4.3 API Contract (FastAPI Endpoints)

```
Base URL: http://localhost:8100/api/v1

GET /health
  Response: { "status": "ok", "db": "connected", "version": "0.1.0" }

GET /stats/summary
  Query params:
    - start: ISO 8601 datetime (default: 24h ago)
    - end: ISO 8601 datetime (default: now)
    - org_id: optional string
    - group_by: "provider" | "model" | "task_type" (default: "model")
  Response:
    {
      "period": { "start": "...", "end": "..." },
      "total_requests": 14523,
      "total_cost_usd": 127.45,
      "total_tokens": 4521000,
      "failure_rate": 0.012,
      "groups": [
        {
          "key": "claude-opus-4",
          "request_count": 8200,
          "total_cost_usd": 98.30,
          "avg_latency_ms": 1243.5,
          "p95_latency_ms": 3100.0,
          "avg_cost_per_request_usd": 0.012,
          "total_prompt_tokens": 2100000,
          "total_completion_tokens": 890000,
          "failure_count": 45
        }
      ]
    }

GET /stats/timeseries
  Query params:
    - start: ISO 8601 datetime
    - end: ISO 8601 datetime
    - interval: "1h" | "6h" | "1d" (default: "1h")
    - metric: "cost" | "requests" | "latency" | "tokens" (default: "cost")
    - model: optional string filter
    - org_id: optional string
  Response:
    {
      "metric": "cost",
      "interval": "1h",
      "data": [
        { "timestamp": "2026-03-03T00:00:00Z", "value": 12.34 },
        { "timestamp": "2026-03-03T01:00:00Z", "value": 8.91 }
      ]
    }

GET /stats/top-traces
  Query params:
    - start: ISO 8601 datetime
    - end: ISO 8601 datetime
    - sort_by: "cost" | "tokens" | "latency" (default: "cost")
    - limit: integer (default: 10, max: 100)
    - org_id: optional string
  Response:
    {
      "traces": [
        {
          "trace_id": "abc-123",
          "total_cost_usd": 4.56,
          "total_tokens": 45000,
          "total_latency_ms": 12340,
          "event_count": 7,
          "models_used": ["claude-opus-4", "claude-haiku-3.5"],
          "first_event_at": "2026-03-03T10:15:00Z",
          "last_event_at": "2026-03-03T10:15:45Z",
          "agent_framework": "langchain"
        }
      ]
    }

GET /stats/waste-score
  Query params:
    - start: ISO 8601 datetime
    - end: ISO 8601 datetime
    - org_id: optional string
  Response:
    {
      "waste_score": 0.34,
      "total_potential_savings_usd": 42.10,
      "breakdown": [
        {
          "task_type": "classification",
          "current_model": "claude-opus-4",
          "suggested_model": "claude-haiku-3.5",
          "call_count": 230,
          "current_cost_usd": 18.40,
          "projected_cost_usd": 1.15,
          "savings_usd": 17.25,
          "confidence": 0.85
        }
      ]
    }

GET /events
  Query params:
    - start: ISO 8601 datetime
    - end: ISO 8601 datetime
    - model: optional string
    - provider: optional string
    - task_type: optional TaskType
    - status: optional "success" | "failure"
    - trace_id: optional string
    - session_id: optional string
    - org_id: optional string
    - limit: integer (default: 50, max: 500)
    - offset: integer (default: 0)
  Response:
    {
      "events": [ LLMEvent, ... ],
      "total_count": 14523,
      "has_more": true
    }

GET /events/{event_id}
  Response: LLMEvent (full record)
```

### 4.4 Classification Output Format

The classifier produces a `ClassificationResult` that gets merged into the `LLMEvent` before storage.

```python
# src/agentproof/classifier/taxonomy.py

from pydantic import BaseModel
from agentproof.types import TaskType


class ClassificationResult(BaseModel):
    task_type: TaskType
    confidence: float               # 0.0 to 1.0
    signals: list[str]              # Which rules/features triggered the classification
    # Examples of signals:
    # "tool_array_present", "code_fence_in_system", "output_token_ratio_low",
    # "classify_keyword_in_prompt", "json_schema_in_system"


class ClassifierInput(BaseModel):
    """What the classifier receives from the callback. Hashed content only
    for ML model; structural metadata for rules engine."""
    system_prompt_hash: str | None
    has_tools: bool
    tool_count: int
    has_json_schema: bool           # output format constraint
    has_code_fence_in_system: bool  # code blocks in system prompt
    prompt_token_count: int
    completion_token_count: int
    token_ratio: float              # completion_tokens / prompt_tokens
    model: str
    # For rules engine: raw structural signals extracted before hashing
    system_prompt_keywords: list[str]  # extracted keywords (classify, summarize, extract, etc.)
    output_format_hint: str | None     # "json", "markdown", "code", etc.
```

### 4.5 Docker Compose Stack (Local Dev)

```yaml
# docker-compose.yml
services:
  db:
    image: timescale/timescaledb:2.17.2-pg16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: agentproof
      POSTGRES_USER: agentproof
      POSTGRES_PASSWORD: localdev
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./src/agentproof/pipeline/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agentproof"]
      interval: 5s
      timeout: 5s
      retries: 5

  litellm:
    image: ghcr.io/berriai/litellm:main-v1.61.20
    ports:
      - "4000:4000"
    volumes:
      - ./litellm-config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    environment:
      LITELLM_MASTER_KEY: sk-local-dev-key
      DATABASE_URL: postgresql://agentproof:localdev@db:5432/agentproof
    depends_on:
      db:
        condition: service_healthy

  api:
    build:
      context: .
      dockerfile: Dockerfile
      target: dev
    ports:
      - "8100:8100"
    volumes:
      - ./src:/app/src
    environment:
      DATABASE_URL: postgresql+asyncpg://agentproof:localdev@db:5432/agentproof
      AGENTPROOF_ENV: development
    depends_on:
      db:
        condition: service_healthy
    command: ["uvicorn", "agentproof.api.app:app", "--host", "0.0.0.0", "--port", "8100", "--reload"]

volumes:
  pgdata:
```

---

## 5. Phase 0 to Phase 1 Handoff Points

### Interfaces That Must Be Frozen Before Phase 1 Starts

Phase 1 initiatives (1A, 1D, 1E) build directly on Phase 0 outputs. The following must be stable and versioned:

#### 5.1 Frozen Interface: LLMEvent Schema (v1)

- The `LLMEvent` Pydantic model defined in section 4.1 must be considered v1-frozen.
- New fields can be added (backward compatible). Existing fields cannot be renamed, retyped, or removed.
- The database schema must match. Alembic migration history must be clean.
- **Gate:** All 0A tasks complete, 0A-5 load test passes, schema is documented in code and in this ADR.

#### 5.2 Frozen Interface: Callback Handler Plugin API

- The `AgentProofCallback` class must expose a stable constructor signature:
  ```python
  AgentProofCallback(
      db_url: str,
      org_id: str | None = None,
      enable_classification: bool = True,
      batch_size: int = 50,
      flush_interval_ms: int = 100,
  )
  ```
- 1D (MCP tracing) extends this callback. 1A (benchmarking) adds traffic mirroring alongside it. Both need a stable base class.
- **Gate:** 0A-2, 0A-3, 0A-4 complete.

#### 5.3 Frozen Interface: Task Taxonomy (v1)

- The `TaskType` enum must be locked for Phase 1 planning. 1A benchmarking rubrics and 1B waste detection both key off task types.
- New types can be added, but the existing seven types and their string representations must not change.
- **Gate:** 0B-3 evaluation complete, final taxonomy reviewed by ml + be2.

#### 5.4 Frozen Interface: API Contract (v1)

- The REST endpoints defined in section 4.3 must be stable for the dashboard.
- 1E (Alerts & Budgets) will add new endpoints but depends on existing ones working correctly.
- **Gate:** 0C-4 complete, integration tests passing.

#### 5.5 Frozen Interface: Database Schema (v1)

- The `llm_events` and `tool_calls` tables, plus all continuous aggregates, must be stable.
- 1A adds new tables (benchmark results). 1D adds new columns or tables (MCP execution graph). Neither should require altering existing tables.
- **Gate:** 0A-1 complete, migration history clean, compression and retention policies tested.

### Phase 1 Readiness Checklist

Before any Phase 1 task begins, verify:

- [ ] All 0A tasks are marked done
- [ ] 0B classifier is integrated and producing task types on live events
- [ ] Docker Compose local dev works end-to-end (LLM call -> callback -> DB -> API -> dashboard)
- [ ] CI passes (unit + integration tests green)
- [ ] Load test (0A-5) confirms <8ms P95 overhead
- [ ] At least one integration guide (0D-1 or 0D-2) validated externally
- [ ] This ADR is updated with any deviations from the original plan

---

## 6. Risk Register

### R1: LiteLLM Callback Overhead Exceeds Target

- **Likelihood:** Medium
- **Impact:** High (if logging slows LLM calls, users will not adopt)
- **Description:** The <8ms P95 overhead target may be hard to hit if DB writes or classification add latency to the callback path.
- **Mitigation:** The callback must be fully fire-and-forget. Use an in-memory buffer that batches writes (asyncio.Queue + background task). The callback method itself should only enqueue an event object, never await a DB write. Classification runs on the dequeued event, not in the request path. If the buffer fills (backpressure), drop events and log a warning rather than blocking.

### R2: TimescaleDB Continuous Aggregate Complexity

- **Likelihood:** Medium
- **Impact:** Medium (dashboard performance, development velocity)
- **Description:** TimescaleDB continuous aggregates have restrictions (no JOINs, limited function support). The aggregate definitions in section 4.2 may need revision when we discover real query patterns.
- **Mitigation:** Start with raw table queries in the API layer. Add continuous aggregates incrementally as we identify slow queries. The schema.sql defines them but they can be dropped and recreated without data loss.

### R3: Task Classifier Accuracy Insufficient for Waste Detection

- **Likelihood:** Medium
- **Impact:** High (waste score is a core value prop; inaccurate classification undermines trust)
- **Description:** The rules-based classifier may misclassify complex prompts that blend task types (e.g., a prompt that asks for code generation AND classification). The synthetic eval dataset may not represent real-world prompt distribution.
- **Mitigation:** Ship with confidence scores. Only include high-confidence classifications in waste score calculations (>0.7). Build a feedback mechanism in Phase 1 where users can correct classifications. Track classifier accuracy as a first-class metric. The ML evaluation (0B-3) serves as a backup; if rules-based accuracy is below 75% on the eval set, invest in the DistilBERT approach.

### R4: LiteLLM Version Drift

- **Likelihood:** Medium
- **Impact:** Medium (callback API could change, breaking our handler)
- **Description:** LiteLLM releases frequently. The `CustomLogger` base class interface or the event payload structure could change between versions, breaking our callback handler.
- **Mitigation:** Pin LiteLLM to a specific version in `pyproject.toml` (start with 1.61.x). Add a dedicated integration test that verifies callback contract compatibility. Upgrade LiteLLM deliberately, not automatically. Subscribe to LiteLLM release notes.

### R5: Docker Compose Dev Environment Fragility

- **Likelihood:** Low
- **Impact:** Medium (blocks all local development)
- **Description:** Docker Compose stacks with multiple services tend to accumulate state issues (orphaned volumes, port conflicts, stale images).
- **Mitigation:** Provide a `make reset` target that does a full teardown and rebuild. Document common issues in a TROUBLESHOOTING.md. Use named volumes with deterministic names. Health checks on all services prevent startup race conditions.

### R6: Parallel Development Integration Failures

- **Likelihood:** High
- **Impact:** Medium (sprint velocity loss from integration debugging)
- **Description:** With six engineers working in parallel on four initiatives, the integration points between 0A/0B and 0A/0C will be where things break. Schema mismatches, missing fields, type misalignment.
- **Mitigation:** This ADR defines the interface contracts. The `LLMEvent` Pydantic model is the source of truth, and both the callback writer and the API reader import it. The schema.sql must be generated/validated against the Pydantic model (add a test that checks they are in sync). Require PR reviews from the adjacent team whenever an interface-touching file changes. Run integration tests in CI on every PR.

### R7: SHA-256 Hashing Determinism Across Frameworks

- **Likelihood:** Medium
- **Impact:** Low (affects trace correlation, not core functionality)
- **Description:** Different agent frameworks serialize messages differently. The SHA-256 hash of "the same prompt" may differ depending on whether it came through LangChain vs Claude Code because of JSON key ordering, whitespace, or metadata fields.
- **Mitigation:** Define a canonical serialization format for hashing. Sort JSON keys, strip whitespace, exclude metadata fields. Document the exact algorithm in `hasher.py`. The hash is for content-addressable deduplication, not cryptographic proof (that comes in Phase 2).

### R8: Scope Creep in Phase 0

- **Likelihood:** High
- **Impact:** Medium (delays Phase 1 start, wastes early momentum)
- **Description:** It is tempting to add features during Phase 0 (auth, multi-tenancy, advanced analytics). Every addition delays the critical path.
- **Mitigation:** Phase 0 is localhost-only, single-tenant, no auth. The only goal is: LLM call goes in, classified event comes out, user sees it in CLI/dashboard. Anything else gets filed as a Phase 1+ task. The project board enforces this; do not add tasks to 0A-0D that are not already listed.

### R9: Agent Framework Detection Unreliability

- **Likelihood:** Medium
- **Impact:** Low (nice-to-have metadata, not critical path)
- **Description:** Detecting which agent framework initiated a call (0A-4) depends on HTTP headers, user-agent strings, or metadata that frameworks may not consistently provide.
- **Mitigation:** Treat `agent_framework` as optional/best-effort. Use a detection hierarchy: explicit metadata > user-agent header > LiteLLM model group naming convention > null. Do not block any feature on this field being populated.

### R10: ONNX Runtime Build Complexity (If ML Classifier Adopted)

- **Likelihood:** Low (only relevant if ML path chosen in 0B-3)
- **Impact:** Medium (adds build complexity, platform-specific issues)
- **Description:** ONNX Runtime has platform-specific wheels and can cause CI/Docker build issues, especially on ARM64 (Apple Silicon dev machines vs x86 CI).
- **Mitigation:** If ML approach is adopted, use the `onnxruntime` CPU-only package. Test the Docker build on both amd64 and arm64. If build issues arise, fall back to the rules-based classifier and revisit ML in Phase 1 when the team has more infrastructure maturity.

---

## 7. Estimation and Implementation Plan

### Phase 0 Overall

| Metric | Estimate |
|--------|----------|
| Duration | 6 weeks (3 two-week sprints) |
| Team size | 6 engineers |
| Total effort | ~30 person-weeks |
| Critical path | 5 weeks (0A pipeline -> 0B integration -> 0C waste score) |

### Per-Initiative Sizing

| ID | Initiative | Size | Effort (person-weeks) | Calendar Weeks |
|----|-----------|------|----------------------|----------------|
| 0A | Core Data Pipeline | L | 7-8 | 5 (2 people) |
| 0B | Task Classification | M | 4-5 | 4 (1 person) |
| 0C | CLI + Dashboard MVP | M | 5-6 | 5 (1 person) |
| 0D | Initial Integrations | M | 4-5 | 4 (1 person) |

### Per-Task Sizing

| Task | Size | Hours | Notes |
|------|------|-------|-------|
| 0A-1 | M | 16-24 | Schema design, TimescaleDB setup, aggregates, migration |
| 0A-2 | L | 24-32 | Core callback handler, async buffering, error handling |
| 0A-3 | S | 8-12 | Hashing is straightforward; canonical serialization needs care |
| 0A-4 | M | 16-24 | Trace context propagation, framework detection heuristics |
| 0A-5 | M | 16-24 | Load test harness, profiling, tuning |
| 0A-6 | S | 8-12 | Docker Compose, health checks, init scripts |
| 0B-1 | S | 4-8 | Taxonomy definition, documentation |
| 0B-2 | M | 16-24 | Rules engine with signal extraction |
| 0B-3 | L | 24-32 | DistilBERT fine-tune, ONNX export, benchmark vs rules |
| 0B-4 | M | 12-16 | Synthetic dataset across 7 task types |
| 0B-5 | M | 12-16 | Integration with callback pipeline, testing |
| 0C-1 | S | 8-12 | Typer CLI, Rich output formatting |
| 0C-2 | S | 8-12 | Waste score heuristic (depends on classifier) |
| 0C-3 | M | 20-28 | React SPA, Recharts, 4 chart types, responsive layout |
| 0C-4 | M | 16-24 | FastAPI endpoints, query builders, Pydantic schemas |
| 0D-1 | S | 6-10 | Claude Code config-only integration |
| 0D-2 | S | 6-10 | OpenCode config-only integration |
| 0D-3 | M | 12-16 | LangChain needs SDK-level integration |
| 0D-4 | M | 12-16 | CrewAI integration + validation |
| 0D-5 | M | 16-24 | Dockerized test harness for all frameworks |

### Success Metrics for Phase 0

| Metric | Target | Measurement |
|--------|--------|-------------|
| Callback overhead P95 | <8ms | 0A-5 load test with 100 concurrent sessions |
| Classifier accuracy | >75% on synthetic eval set | 0B-3 evaluation run |
| Classifier latency P95 | <5ms | Benchmark in 0B-3 |
| API response time P95 | <200ms for summary queries | Load test against continuous aggregates |
| Integration guide completeness | 4 frameworks documented | 0D-1 through 0D-4 |
| Integration test pass rate | 100% in CI | 0D-5 automated suite |
| Docker Compose cold start | <60 seconds to healthy | Measured from `docker compose up` |
| Test coverage (pipeline) | >80% line coverage | pytest-cov report |
| Test coverage (classifier) | >80% line coverage | pytest-cov report |
| Test coverage (API/CLI) | >60% line coverage | pytest-cov report |

### Rollout Strategy

Phase 0 is internal tooling. There is no external rollout. The "release" is:

1. Docker Compose stack works end-to-end on every team member's machine.
2. CI pipeline is green on `main`.
3. One real LLM workflow (e.g., Claude Code session) has been traced end-to-end through the pipeline, classified, and displayed in both CLI and dashboard.
4. Integration guides are reviewed by someone who did not write them.

---

## Consequences

### What We Get

- A concrete, parallelizable plan for six engineers across four initiatives.
- Frozen interface contracts that prevent integration drift.
- A clear critical path (0A -> 0B -> 0C) and understanding of which work is independent.
- Technology choices that optimize for Python ecosystem coherence and development speed.
- A risk register with specific mitigations, not vague concerns.

### What We Accept

- Python for the full backend means no type-safe compile step. We mitigate with mypy strict mode and Pydantic runtime validation.
- Monorepo means all six engineers are in one codebase. We mitigate with clear directory boundaries and CODEOWNERS.
- TimescaleDB adds operational complexity vs plain Postgres. We accept this because time-series queries are the primary access pattern and continuous aggregates will be critical for dashboard performance.
- The rules-based classifier ships first, which means initial waste scores may be less accurate than the ML approach. We accept this because shipping fast with a fallback is better than blocking on ML evaluation.
- No auth or multi-tenancy in Phase 0. This is deliberate. Adding it now would delay Phase 1 by 2+ weeks and is unnecessary for local-only usage.

---

## Appendix A: LiteLLM Callback Handler Skeleton

Reference implementation for 0A-2. This is not production code but establishes the pattern:

```python
# src/agentproof/pipeline/callback.py

import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from agentproof.classifier.taxonomy import ClassificationResult
from agentproof.types import EventStatus, LLMEvent, ToolCallRecord


class AgentProofCallback(CustomLogger):
    """Async LiteLLM callback that captures events to TimescaleDB.

    All DB writes happen via a background task that drains an in-memory
    buffer. The callback methods themselves only enqueue and never await
    IO, guaranteeing near-zero overhead on the LLM request path.
    """

    def __init__(
        self,
        db_url: str,
        org_id: str | None = None,
        enable_classification: bool = True,
        batch_size: int = 50,
        flush_interval_ms: int = 100,
    ) -> None:
        self._db_url = db_url
        self._org_id = org_id
        self._enable_classification = enable_classification
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_ms / 1000.0
        self._queue: asyncio.Queue[LLMEvent] = asyncio.Queue(maxsize=10_000)
        self._writer_task: asyncio.Task | None = None
        # Classifier and DB engine initialized lazily to avoid import-time IO
        self._classifier = None
        self._engine = None

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: datetime, end_time: datetime
    ) -> None:
        event = self._build_event(kwargs, response_obj, start_time, end_time, EventStatus.SUCCESS)
        self._enqueue(event)

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: datetime, end_time: datetime
    ) -> None:
        event = self._build_event(kwargs, response_obj, start_time, end_time, EventStatus.FAILURE)
        self._enqueue(event)

    def _enqueue(self, event: LLMEvent) -> None:
        """Non-blocking enqueue. Drops event if buffer is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # TODO: increment a dropped_events counter for observability
            pass

    def _build_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
        status: EventStatus,
    ) -> LLMEvent:
        """Transform LiteLLM callback args into our canonical LLMEvent."""
        # Implementation extracts fields from kwargs and response_obj,
        # hashes content, detects agent framework, extracts tool calls.
        # Full implementation in 0A-2.
        ...

    @staticmethod
    def _hash_content(content: str) -> str:
        """Canonical SHA-256 hash: sort keys if JSON, strip whitespace."""
        try:
            parsed = json.loads(content)
            canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            canonical = content.strip()
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

## Appendix B: Configuration Schema

```python
# src/agentproof/config.py

from pydantic_settings import BaseSettings


class AgentProofConfig(BaseSettings):
    """All configuration via environment variables, with sensible defaults."""

    model_config = {"env_prefix": "AGENTPROOF_"}

    # Database
    database_url: str = "postgresql+asyncpg://agentproof:localdev@localhost:5432/agentproof"

    # Pipeline
    pipeline_batch_size: int = 50
    pipeline_flush_interval_ms: int = 100
    pipeline_queue_max_size: int = 10_000
    pipeline_enable_classification: bool = True

    # Classifier
    classifier_confidence_threshold: float = 0.7
    classifier_use_ml: bool = False  # Switch to True after 0B-3 evaluation

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_cors_origins: list[str] = ["http://localhost:5173"]  # Vite dev server

    # General
    env: str = "development"  # development | staging | production
    log_level: str = "INFO"
    org_id: str | None = None
```

## Appendix C: TypeScript API Client Types

For the dashboard team to use from Day 1 with mock data:

```typescript
// dashboard/src/api/types.ts

export interface StatsSummary {
  period: { start: string; end: string };
  total_requests: number;
  total_cost_usd: number;
  total_tokens: number;
  failure_rate: number;
  groups: StatGroup[];
}

export interface StatGroup {
  key: string;
  request_count: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  avg_cost_per_request_usd: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  failure_count: number;
}

export interface TimeseriesResponse {
  metric: "cost" | "requests" | "latency" | "tokens";
  interval: "1h" | "6h" | "1d";
  data: TimeseriesPoint[];
}

export interface TimeseriesPoint {
  timestamp: string;
  value: number;
}

export interface TopTrace {
  trace_id: string;
  total_cost_usd: number;
  total_tokens: number;
  total_latency_ms: number;
  event_count: number;
  models_used: string[];
  first_event_at: string;
  last_event_at: string;
  agent_framework: string | null;
}

export interface WasteScore {
  waste_score: number;
  total_potential_savings_usd: number;
  breakdown: WasteBreakdownItem[];
}

export interface WasteBreakdownItem {
  task_type: string;
  current_model: string;
  suggested_model: string;
  call_count: number;
  current_cost_usd: number;
  projected_cost_usd: number;
  savings_usd: number;
  confidence: number;
}

export interface EventsResponse {
  events: LLMEvent[];
  total_count: number;
  has_more: boolean;
}

export interface LLMEvent {
  id: string;
  created_at: string;
  status: "success" | "failure";
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  trace_id: string;
  span_id: string;
  task_type: string | null;
  task_type_confidence: number | null;
  has_tool_calls: boolean;
  agent_framework: string | null;
}
```
