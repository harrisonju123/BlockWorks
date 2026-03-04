# ADR-003 --- Phase 1 Architecture Plan

**Date:** 2026-03-03
**Status:** Proposed
**Authors:** Principal Architect
**Scope:** Phase 1 hardening, parallel execution plan, schema additions, new models, and key technical decisions

---

## Context

Phase 0 delivered the data pipeline (`llm_events`, `tool_calls`), the rules-based task classifier, the REST API, and the dashboard. Phase 1 builds on that foundation with five initiatives: cross-provider benchmarking (1A), waste detection (1B), smart routing (1C), MCP tracing (1D), and alerts/budgets (1E). Six engineers, ten weeks, three parallel tracks.

This document is the single source of truth for Phase 1 implementation. It covers what to fix before we start (Week 0), how the work parallelizes, every new table and model, and the technical decisions that constrain implementation.

---

## 1. Hardening Sprint (Week 0)

Three issues identified during Phase 0 review must be resolved before new feature work begins. All three are assigned to be1 and be2 and should complete within a single week.

### 1a. EventWriter Graceful Shutdown

**Problem:** `EventWriter.run()` loops with `while True` and has no shutdown path. On SIGTERM the process dies, dropping whatever is in the queue and potentially mid-flush.

**Fix:** Add a `shutdown` method that signals the loop to stop, drains remaining events, and closes the connection pool.

```python
# In writer.py --- additions to EventWriter

class EventWriter:
    def __init__(self, ...) -> None:
        # ... existing init ...
        self._shutdown_event: asyncio.Event = asyncio.Event()

    async def shutdown(self) -> None:
        """Signal the writer to drain and stop."""
        self._shutdown_event.set()

    async def run(self) -> None:
        pool = await self._ensure_pool()
        batch: list[LLMEvent] = []

        while not self._shutdown_event.is_set():
            try:
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=self._flush_interval_s
                    )
                    batch.append(event)
                except asyncio.TimeoutError:
                    pass

                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    await self._flush_with_retry(pool, batch)
                    batch = []

            except Exception:
                logger.exception("EventWriter unexpected error")
                batch = []
                await asyncio.sleep(1.0)

        # Drain phase: flush everything remaining in the queue
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._flush_with_retry(pool, batch)
            logger.info("Drained %d events during shutdown", len(batch))

        if self._pool:
            await self._pool.close()
        logger.info("EventWriter shut down cleanly")
```

The callback's `_ensure_writer` already holds a reference to the task. Register a SIGTERM handler in the API entrypoint:

```python
import signal

async def on_shutdown():
    if callback._writer:
        await callback._writer.shutdown()
        # Give the run() coroutine time to finish its drain
        if callback._writer_task:
            await asyncio.wait_for(callback._writer_task, timeout=10.0)
```

### 1b. Wire queries.py to Continuous Aggregates

**Problem:** `get_summary_stats`, `get_timeseries`, and `get_waste_analysis` all query `llm_events` directly. The continuous aggregates (`hourly_model_stats`, `hourly_task_stats`, `daily_summary`) defined in `schema.sql` are never read.

**Fix:** Rewrite each query function to read from the appropriate aggregate, falling back to `llm_events` only when the requested time range includes the current incomplete bucket (the `end_offset` gap).

The key change for `get_timeseries`:

```python
async def get_timeseries(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    interval: str = "1h",
    metric: str = "cost",
    model: str | None = None,
    org_id: str | None = None,
) -> list[dict]:
    # For hourly intervals, read from hourly_model_stats
    # For daily intervals, read from daily_summary
    # Both are pre-aggregated and orders of magnitude faster

    if interval == "1d":
        source_view = "daily_summary"
        bucket_col = "bucket"
    else:
        source_view = "hourly_model_stats"
        bucket_col = "bucket"

    agg_metric_map = {
        "cost": "SUM(total_cost)",
        "requests": "SUM(request_count)",
        "latency": "SUM(avg_latency_ms * request_count) / NULLIF(SUM(request_count), 0)",
        "tokens": "SUM(total_prompt_tokens + total_completion_tokens)",
    }
    # ... build query against source_view using bucket_col ...
```

Similarly, `get_waste_analysis` should read from `daily_summary` since it groups by `(task_type, model)` which that view already contains.

### 1c. Parameterize pg_interval in SQL

**Problem:** In `get_timeseries`, the `pg_interval` value is string-interpolated directly into the SQL query (`f"time_bucket('{pg_interval}', ...)"`) rather than parameterized. This is not exploitable today because the value comes from a hardcoded allowlist, but it sets a bad precedent and will bite us when we add user-configurable intervals for alert rules.

**Fix:** TimescaleDB's `time_bucket` accepts an `INTERVAL` type. Pass it as a bound parameter:

```python
pg_interval = interval_map.get(interval, "1 hour")

query = text("""
    SELECT
        time_bucket(CAST(:bucket_interval AS INTERVAL), created_at) AS timestamp,
        ...
    FROM hourly_model_stats
    WHERE bucket >= :start AND bucket < :end
    ...
""")

params = {"bucket_interval": pg_interval, "start": start, "end": end}
```

This applies to every call site that uses `time_bucket`, including the new queries we will write for anomaly detection and budget tracking.

---

## 2. Parallel Execution Plan

### Dependency Graph

```
             Week 0          Weeks 1-4          Weeks 5-8          Weeks 9-10
            --------        ----------         ----------         -----------
            Harden    -->   1A (bench)    -->   1B (waste)    -->  Integration
                      -->   1D (MCP)      -->   1D-3/4 (analytics)
                      -->   1E (alerts)   -->   1C (routing)  -->  Integration
                                                ^
                                                |
                                          1A fitness matrix
                                          must be queryable
```

**Critical path:** 1A must produce a queryable fitness matrix by end of Week 4. Both 1B and 1C depend on it. If 1A slips, 1B and 1C slip in lockstep.

**Unblocked tracks (Weeks 1-4):** 1A, 1D, and 1E can start simultaneously because none depend on each other.

### Sprint Allocation (Team of 6)

| Engineer | Sprint 1 (W1-2)      | Sprint 2 (W3-4)      | Sprint 3 (W5-6)      | Sprint 4 (W7-8)      | Sprint 5 (W9-10)     |
|----------|----------------------|----------------------|----------------------|----------------------|----------------------|
| be1      | 1D-1: callback ext   | 1D-2: exec graph     | 1D-3: perf analytics | 1D-4: cost attrib    | Integration + docs   |
| be2      | 1A-1: traffic mirror | 1A-5: anonymization  | 1C-1: routing DSL    | 1C-2: router hook    | 1C-3: realtime route |
| ml1      | 1A-2: judge framework| 1A-3: rubric schemas | 1B-1: overkill detect| 1B-2: redundant calls| 1B-4: cache miss det |
| ml2      | 1A-4: fitness matrix | 1A-6: cross-org agg  | 1B-3: context bloat  | 1B-5: loop detector  | 1B-6: waste reports  |
| infra    | 1E-1: spend tracking | 1E-4: anomaly detect | 1E-5: LiteLLM budget | 1C-5: A/B framework  | 1E-3: budget caps    |
| fe       | 1E-2: Slack/email    | Dashboard: bench tab | Dashboard: MCP tab   | 1C-4: dry-run mode   | Dashboard: alerts    |

### Sprint Goals (Definition of Done)

| Sprint | Goal | Verification |
|--------|------|--------------|
| S1 | Traffic mirroring running, MCP callback capturing tool_use blocks, spend agg query works, Slack webhook delivers | Integration test for each; manual Slack message confirmed |
| S2 | LLM-as-judge scoring a batch, fitness matrix updating, execution graph stored, anomaly z-score computed | Benchmark result rows in DB; graph query returns DAG |
| S3 | Waste detectors 1-3 producing output, routing DSL parsing + validating, budget thresholds wired | Waste API returns breakdown; YAML policy loads without error |
| S4 | All five waste detectors running, router making live decisions in shadow mode, loop detector tested | Shadow routing log shows decisions; waste score matches expectations |
| S5 | Full integration: routing uses live fitness matrix, budget cap triggers downgrade, MCP cost attribution works | End-to-end test: send traffic, see bench results, waste flags, MCP graph, alert fires |

---

## 3. New Database Schema

All new tables live alongside `llm_events` and `tool_calls`. No alterations to existing tables.

```sql
-- ============================================================
-- Phase 1 Schema Additions
-- Applied as a migration, not in the init script.
-- ============================================================

-- 3a. Benchmark results (1A)
-- One row per (model, task_type, judge_run) combination.
CREATE TABLE benchmark_results (
    id              UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    model           TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    quality_score   DOUBLE PRECISION NOT NULL CHECK (quality_score BETWEEN 0.0 AND 1.0),
    cost_usd        DOUBLE PRECISION NOT NULL,
    latency_ms      DOUBLE PRECISION NOT NULL,
    judge_model     TEXT NOT NULL DEFAULT 'claude-haiku-4-5-20251001',
    sample_size     INTEGER NOT NULL,
    prompt_hash     TEXT NOT NULL,       -- links back to the original request
    org_id          TEXT,                -- NULL for cross-org aggregates
    rubric_version  TEXT NOT NULL,       -- tracks rubric changes over time

    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('benchmark_results', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_bench_model_task ON benchmark_results (model, task_type, created_at DESC);
CREATE INDEX idx_bench_org ON benchmark_results (org_id, created_at DESC)
    WHERE org_id IS NOT NULL;

-- Continuous aggregate: fitness matrix (model x task_type -> avg scores)
CREATE MATERIALIZED VIEW fitness_matrix
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at)    AS bucket,
    model,
    task_type,
    AVG(quality_score)                  AS avg_quality,
    AVG(cost_usd)                       AS avg_cost,
    AVG(latency_ms)                     AS avg_latency,
    SUM(sample_size)                    AS total_samples
FROM benchmark_results
GROUP BY bucket, model, task_type
WITH NO DATA;

SELECT add_continuous_aggregate_policy('fitness_matrix',
    start_offset  => INTERVAL '3 days',
    end_offset    => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day');


-- 3b. MCP calls (1D)
-- One row per MCP tool invocation observed in an LLM response.
CREATE TABLE mcp_calls (
    id              UUID NOT NULL,
    event_id        UUID NOT NULL,       -- FK-by-convention to llm_events.id
    created_at      TIMESTAMPTZ NOT NULL,
    server_name     TEXT NOT NULL,
    method          TEXT NOT NULL,
    params_hash     TEXT NOT NULL,
    response_hash   TEXT,
    latency_ms      DOUBLE PRECISION,
    response_tokens INTEGER,             -- tokens consumed by the MCP response in context
    error           TEXT,

    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('mcp_calls', 'created_at',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_mcp_event ON mcp_calls (event_id, created_at DESC);
CREATE INDEX idx_mcp_server ON mcp_calls (server_name, created_at DESC);
CREATE INDEX idx_mcp_method ON mcp_calls (server_name, method, created_at DESC);


-- 3c. MCP execution graph (1D)
-- Stores DAG edges between MCP calls within a trace.
-- A "parent" is the call whose output was consumed before the "child" was invoked.
CREATE TABLE mcp_execution_graph (
    trace_id        TEXT NOT NULL,
    parent_call_id  UUID NOT NULL,
    child_call_id   UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    ordinal         INTEGER NOT NULL,    -- sequence position within the trace

    PRIMARY KEY (trace_id, parent_call_id, child_call_id)
);

CREATE INDEX idx_mcp_graph_trace ON mcp_execution_graph (trace_id);
CREATE INDEX idx_mcp_graph_parent ON mcp_execution_graph (parent_call_id);


-- 3d. Alert rules (1E)
CREATE TABLE alert_rules (
    id              UUID NOT NULL PRIMARY KEY,
    org_id          TEXT NOT NULL,
    rule_type       TEXT NOT NULL CHECK (rule_type IN (
        'spend_threshold', 'anomaly_zscore', 'error_rate', 'latency_p95'
    )),
    threshold       JSONB NOT NULL,      -- structure varies by rule_type
    channel         TEXT NOT NULL CHECK (channel IN ('slack', 'email', 'both')),
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_alert_rules_org ON alert_rules (org_id) WHERE enabled;


-- 3e. Budget configurations (1E)
CREATE TABLE budget_configs (
    id              UUID NOT NULL PRIMARY KEY,
    org_id          TEXT NOT NULL,
    project_id      TEXT,                -- NULL means org-wide
    budget_usd      DOUBLE PRECISION NOT NULL,
    period          TEXT NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
    action          TEXT NOT NULL CHECK (action IN ('alert', 'downgrade', 'block')),
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (org_id, project_id, period)
);

CREATE INDEX idx_budget_org ON budget_configs (org_id) WHERE enabled;


-- 3f. Alert history (1E)
CREATE TABLE alert_history (
    id              UUID NOT NULL,
    rule_id         UUID NOT NULL,       -- references alert_rules.id
    triggered_at    TIMESTAMPTZ NOT NULL,
    message         TEXT NOT NULL,
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,                -- 'auto' or user_id
    context         JSONB,               -- snapshot of the data that triggered the alert

    PRIMARY KEY (id, triggered_at)
);

SELECT create_hypertable('alert_history', 'triggered_at',
    chunk_time_interval => INTERVAL '7 days');

CREATE INDEX idx_alert_hist_rule ON alert_history (rule_id, triggered_at DESC);
CREATE INDEX idx_alert_hist_open ON alert_history (resolved_at)
    WHERE resolved_at IS NULL;


-- Compression and retention for new hypertables
SELECT add_compression_policy('benchmark_results', INTERVAL '7 days');
SELECT add_compression_policy('mcp_calls', INTERVAL '7 days');
SELECT add_compression_policy('alert_history', INTERVAL '30 days');

SELECT add_retention_policy('benchmark_results', INTERVAL '180 days');
SELECT add_retention_policy('mcp_calls', INTERVAL '90 days');
SELECT add_retention_policy('alert_history', INTERVAL '365 days');
```

### Why These Choices

- **`benchmark_results` is a hypertable** because it grows with every judge run and we need time-range queries for the fitness matrix aggregate. 180-day retention because benchmark history has long-term strategic value.
- **`mcp_execution_graph` is a regular table** (not a hypertable) because the DAG structure is queried by trace_id, not by time range. Recursive CTEs on hypertables have edge cases.
- **`alert_rules` and `budget_configs` are regular tables** because they are configuration, not time-series. Low cardinality, updated infrequently, queried by org_id.
- **`alert_history` is a hypertable** because it grows unbounded and we need time-range queries for the alert dashboard.

---

## 4. New Pydantic Models

These go in `/Users/hju/Documents/BlockWorks/src/agentproof/types.py` alongside the existing `LLMEvent`.

```python
from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BenchmarkResult(BaseModel):
    """Output of a single LLM-as-judge evaluation run."""

    id: UUID
    created_at: datetime
    model: str
    task_type: TaskType
    quality_score: float = Field(ge=0.0, le=1.0)
    cost_usd: float
    latency_ms: float
    judge_model: str = "claude-haiku-4-5-20251001"
    sample_size: int
    prompt_hash: str
    org_id: str | None = None
    rubric_version: str


class MCPCall(BaseModel):
    """A single MCP tool invocation observed in an LLM response."""

    id: UUID
    event_id: UUID
    created_at: datetime
    server_name: str
    method: str
    params_hash: str
    response_hash: str | None = None
    latency_ms: float | None = None
    response_tokens: int | None = None
    error: str | None = None


class RuleType(str, enum.Enum):
    SPEND_THRESHOLD = "spend_threshold"
    ANOMALY_ZSCORE = "anomaly_zscore"
    ERROR_RATE = "error_rate"
    LATENCY_P95 = "latency_p95"


class AlertChannel(str, enum.Enum):
    SLACK = "slack"
    EMAIL = "email"
    BOTH = "both"


class AlertRule(BaseModel):
    """User-defined alert trigger configuration."""

    id: UUID
    org_id: str
    rule_type: RuleType
    threshold: dict          # schema depends on rule_type, validated at API layer
    channel: AlertChannel
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BudgetPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class BudgetAction(str, enum.Enum):
    ALERT = "alert"
    DOWNGRADE = "downgrade"
    BLOCK = "block"


class BudgetConfig(BaseModel):
    """Spend cap for an org or project with an enforcement action."""

    id: UUID
    org_id: str
    project_id: str | None = None
    budget_usd: float = Field(gt=0)
    period: BudgetPeriod
    action: BudgetAction
    enabled: bool = True


class RoutingConstraint(BaseModel):
    """A single rule within a routing policy."""

    task_type: TaskType
    strategy: str = Field(pattern=r"^(cheapest|fastest|best_quality)$")
    min_quality: float = Field(ge=0.0, le=1.0, default=0.9)
    fallback_model: str
    max_cost_per_1k: float | None = None
    max_latency_ms: float | None = None


class RoutingPolicy(BaseModel):
    """Complete routing policy for an org, parsed from YAML config."""

    org_id: str
    version: int = 1
    default_model: str
    constraints: list[RoutingConstraint]
    enabled: bool = True
```

### Threshold Schema by Rule Type

The `AlertRule.threshold` field is a dict whose shape depends on `rule_type`. Validated at the API layer, not in the Pydantic model, to avoid a union type that complicates serialization:

```python
# spend_threshold
{"amount_usd": 500.0, "period": "daily"}

# anomaly_zscore
{"z_threshold": 2.5, "baseline_days": 7, "metric": "cost"}

# error_rate
{"max_rate": 0.05, "window_minutes": 60, "min_requests": 100}

# latency_p95
{"max_ms": 5000, "window_minutes": 30, "model": "claude-sonnet-4-20250514"}
```

---

## 5. Key Technical Decisions

### 5a. LLM-as-Judge: Sonnet for Scoring

**Decision:** Use `claude-sonnet-4-6` as the judge model for all benchmark evaluations.

**Why Sonnet:** Upgraded from Haiku (`claude-haiku-4-5-20251001`) to improve evaluation quality on nuanced tasks like code review and reasoning. Judge responses are capped at 256 tokens, so the per-call cost increase is modest. Sonnet provides significantly better agreement with human raters on structured rubrics, especially for multi-criterion evaluations where Haiku showed weakness.

**Rubric format:** One rubric per `TaskType`. Each rubric returns a structured JSON score.

```yaml
# rubrics/code_generation.yaml
task_type: code_generation
judge_model: claude-haiku-4-5-20251001
version: "1.0"
criteria:
  - name: correctness
    weight: 0.5
    prompt: |
      Does the generated code solve the stated problem?
      Score 0.0 (completely wrong) to 1.0 (fully correct).
  - name: style
    weight: 0.3
    prompt: |
      Is the code idiomatic, well-structured, and readable?
      Score 0.0 (unreadable) to 1.0 (exemplary).
  - name: efficiency
    weight: 0.2
    prompt: |
      Is the approach reasonably efficient for the problem size?
      Score 0.0 (pathologically slow) to 1.0 (optimal).
```

The judge receives: the original prompt (hashed for lookup), the model's output, and the rubric. It returns a JSON object with per-criterion scores. The `quality_score` stored in `benchmark_results` is the weighted sum.

### 5b. Traffic Mirroring

**Decision:** Use LiteLLM's built-in request mirroring, not a custom proxy layer.

LiteLLM already supports sending a copy of a request to N additional models via its router config. We configure this per-org with a sample rate:

```yaml
# litellm-config.yaml addition
router_settings:
  enable_pre_call_checks: true

model_list:
  - model_name: "sonnet"
    litellm_params:
      model: "claude-sonnet-4-20250514"
    # ... existing config ...

# Benchmark mirroring (managed dynamically via LiteLLM API)
# Sample 10% of classification traffic to Haiku for benchmarking
```

The sample rate is controlled per `(org_id, task_type)` and stored in a config table, not in the YAML. The callback inspects the `litellm_params.metadata.mirror_request` flag to route benchmark responses to the judge instead of back to the user.

### 5c. MCP Tracing: Extend the Existing Callback

**Decision:** Extend `AgentProofCallback._build_event` to parse MCP tool_use content blocks, not build a separate callback.

MCP tool invocations appear in the LLM response as `tool_use` content blocks with a specific structure. The callback already iterates over `message.tool_calls`. The extension:

```python
# In callback.py _build_event, after the existing tool_calls loop:

# MCP tool calls appear as tool_use blocks in content
# Distinguish from regular tool calls by server_name in the tool name
mcp_calls: list[MCPCall] = []
content_blocks = getattr(message, "content", [])
if isinstance(content_blocks, list):
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use":
            server, method = _parse_mcp_tool_name(block.name)
            if server:
                mcp_calls.append(MCPCall(
                    id=uuid.uuid4(),
                    event_id=event_id,
                    created_at=end_time,
                    server_name=server,
                    method=method,
                    params_hash=hash_content(json.dumps(block.input)),
                ))
```

The `_parse_mcp_tool_name` function splits on the `__` convention that MCP servers use (e.g., `filesystem__read_file` -> server=`filesystem`, method=`read_file`). Response data (hash, latency, tokens) is populated when the tool result comes back in a subsequent callback invocation, matched by `tool_use_id`.

### 5d. Smart Routing Policy DSL

**Decision:** YAML-based policy files, validated at load time against the `RoutingPolicy` Pydantic model.

```yaml
# Example: routing-policy.yaml
org_id: "org_acme"
version: 1
default_model: "claude-sonnet-4-20250514"
constraints:
  - task_type: classification
    strategy: cheapest
    min_quality: 0.90
    fallback_model: "claude-sonnet-4-20250514"

  - task_type: code_generation
    strategy: best_quality
    min_quality: 0.95
    fallback_model: "claude-sonnet-4-20250514"
    max_cost_per_1k: 0.02

  - task_type: summarization
    strategy: cheapest
    min_quality: 0.85
    fallback_model: "claude-haiku-4-5-20251001"

  - task_type: extraction
    strategy: fastest
    min_quality: 0.90
    fallback_model: "gpt-4o-mini"
    max_latency_ms: 1000
```

**Resolution algorithm:** For an incoming request classified as task_type T:
1. Find the constraint matching T. If none, use `default_model`.
2. Query the fitness matrix for all models where `avg_quality >= min_quality` for task_type T.
3. Apply the strategy (`cheapest` sorts by cost, `fastest` by latency, `best_quality` by score).
4. If the top candidate violates `max_cost_per_1k` or `max_latency_ms`, skip it.
5. If no candidate qualifies, use `fallback_model`.

Decision latency target: <2ms. The fitness matrix is cached in-process with a 5-minute TTL, so step 2 is a dict lookup, not a DB query.

### 5e. Anomaly Detection: Z-Score on Rolling Baseline

**Decision:** Z-score against a 7-day rolling baseline, computed from the `daily_summary` continuous aggregate.

```python
async def check_spend_anomaly(
    session: AsyncSession,
    org_id: str,
    current_day_spend: float,
    z_threshold: float = 2.5,
) -> bool:
    """Return True if today's spend is anomalous relative to 7-day baseline."""
    query = text("""
        SELECT
            AVG(total_cost) AS mean_cost,
            STDDEV(total_cost) AS stddev_cost
        FROM daily_summary
        WHERE org_id = :org_id
          AND bucket >= now() - INTERVAL '7 days'
          AND bucket < now() - INTERVAL '1 day'
    """)
    result = await session.execute(query, {"org_id": org_id})
    row = result.fetchone()

    if not row or row.stddev_cost is None or row.stddev_cost == 0:
        return False

    z_score = (current_day_spend - row.mean_cost) / row.stddev_cost
    return z_score > z_threshold
```

We chose z-score over more sophisticated methods (ARIMA, Prophet) because:
- The daily_summary aggregate already exists, so no new data pipeline is needed.
- Spend patterns for most orgs are stable enough that z-score catches the two things we care about: runaway agent loops and misconfigured batch jobs.
- We can always layer on Prophet later without changing the alert_rules schema.

### 5f. Slack and Email Delivery

**Slack:** Incoming webhooks. Each org configures a webhook URL stored in `alert_rules.threshold.webhook_url`. We POST a Block Kit message. No Slack app, no OAuth -- that is Phase 3 territory.

**Email:** SMTP via the existing infrastructure. Alert emails are plain-text with a link to the dashboard. We use a single template with variable substitution, not an HTML email builder.

```python
# Notification dispatch -- simple strategy pattern
async def dispatch_alert(rule: AlertRule, message: str, context: dict) -> None:
    if rule.channel in (AlertChannel.SLACK, AlertChannel.BOTH):
        await send_slack_webhook(
            url=rule.threshold["webhook_url"],
            text=message,
            blocks=format_slack_blocks(rule, context),
        )
    if rule.channel in (AlertChannel.EMAIL, AlertChannel.BOTH):
        await send_email(
            to=rule.threshold["email"],
            subject=f"[AgentProof] {rule.rule_type.value} alert",
            body=message,
        )
```

---

## 6. Phase 1 -> Phase 2 Handoff

Phase 2 introduces attestation (on-chain cost proofs). The following must freeze before attestation work begins:

### Must Freeze

1. **`llm_events` schema.** The attestation hash chain signs over event fields. Any column addition after Phase 2 starts requires a hash version bump and dual-signature support. Do not add columns to `llm_events` in Phase 1 -- that is why MCP data lives in a separate table.

2. **`benchmark_results` schema.** Attestation will reference benchmark quality scores to validate routing decisions. The `rubric_version` field exists specifically so that attested claims can be tied to a known evaluation methodology.

3. **Cost calculation.** The `estimated_cost` field on `llm_events` must use a pinned version of LiteLLM's cost calculator. Phase 2 will hash this cost alongside the event. If the calculator changes between write and attestation, the proof breaks.

4. **Content hashing algorithm.** Currently SHA-256 via `hash_content()`. This becomes the basis for Merkle tree construction. Switching to a different algorithm after Phase 2 starts would require re-hashing all historical data.

5. **Fitness matrix query interface.** The routing engine reads the fitness matrix to make decisions. Attestation will prove that a routing decision was consistent with the matrix state at decision time. The query interface (input: task_type + strategy, output: ranked model list) must be stable.

### Safe to Change

- Alert rules, budget configs, notification channels -- these are operational concerns, not attested.
- MCP tables -- tracing data is observational, not part of the cost attestation chain.
- Waste detection algorithms -- recommendations are advisory, not on-chain.
- Dashboard UI -- entirely decoupled from the attestation layer.

---

## 7. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | **1A slips, blocking 1B + 1C.** The fitness matrix is the critical dependency. If LLM-as-judge rubric design takes longer than expected or Haiku scoring proves unreliable, the entire second half of Phase 1 is delayed. | Medium | High | De-risk in Sprint 1: build a minimal judge pipeline with a single rubric (code_generation) and validate Haiku agreement rate against 50 manually scored samples. If agreement is below 80%, escalate to Sonnet immediately rather than iterating on rubric design. Ship a "static" fitness matrix (hardcoded from manual benchmarks) as a fallback so 1B and 1C can start with stale data. |
| R2 | **MCP tool_use parsing breaks across providers.** The `tool_use` block format is well-defined for Anthropic models but varies for OpenAI function calls and other providers. Our parser may miss MCP calls from non-Anthropic models. | Medium | Medium | Build the parser with a provider-agnostic interface from day one. Define an `MCPToolExtractor` protocol with per-provider implementations. Start with Anthropic, add OpenAI in Sprint 2. Accept that some providers will have partial coverage initially. |
| R3 | **Traffic mirroring doubles cost during benchmarking.** Mirroring sends real requests to alternative models. At 10% sample rate on a high-traffic org, this could be significant. | Low | Medium | Default sample rate is 5%, not 10%. Benchmark requests use `max_tokens=1` where we only need to evaluate whether the model would produce a quality response (for classification tasks). For generative tasks where full output is needed, cap at 2% sample rate. Surface projected benchmark cost in the dashboard before the user enables it. |
| R4 | **Budget enforcement race condition.** Multiple concurrent requests check budget, all see headroom, all proceed, budget exceeded. | Medium | Medium | Use Postgres advisory locks keyed on `(org_id, period)` for the check-and-deduct operation. For the `block` action, accept a small overshoot (up to one request's cost) rather than adding latency to every request. Document this behavior explicitly. |
| R5 | **Continuous aggregate lag causes stale anomaly detection.** The `daily_summary` aggregate has a 1-day `end_offset`, meaning today's data is not in the view. Anomaly detection for "right now" would miss the current spike. | Medium | Medium | For real-time anomaly detection, query `llm_events` directly for the current day and combine with the aggregate for the baseline. This is the one place where querying the raw table is acceptable. The query only scans today's partition (a single chunk), so performance is bounded. |
| R6 | **Routing policy misconfiguration causes quality degradation.** A user sets `min_quality: 0.5` and the router happily sends code generation to the cheapest model. | Low | High | Enforce minimum quality floors per task type that the user cannot override: `code_generation >= 0.8`, `reasoning >= 0.8`, others >= 0.7. Require dry-run mode for any new policy before it goes live. Send a weekly quality digest comparing routed vs. non-routed request outcomes. |
| R7 | **Phase 2 attestation assumptions invalidated.** We freeze the wrong interfaces, or Phase 1 changes something we thought was stable. | Low | High | The "Must Freeze" list in Section 6 is the contract. Add a CI check that fails if any frozen schema or function signature is modified without a `PHASE2_BREAK` label on the PR. Review this list with the attestation team at the Phase 1 midpoint (Week 5). |
