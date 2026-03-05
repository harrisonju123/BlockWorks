# AgentProof

## Project Architecture

This project uses Python (primary backend), Go, TypeScript (dashboard frontend), and YAML configs. The stack includes LiteLLM proxy, Docker Compose, and blockchain/EVM components. When debugging API errors, trace the full request chain: Frontend → AgentProof backend → LiteLLM → upstream model provider.

## Development Workflow

### Task Lifecycle

Every task follows this flow:

1. **Plan** — Use the principal-architect-planner agent before writing code. Identify files to create/modify, interfaces, dependencies, and what can be parallelized.
2. **Build** — Implement the task. Maximize parallel work across independent pieces.
3. **Simplify** — Run `/simplify` after implementation to review for reuse, quality, and efficiency.
4. **Code Review** — Run `/code-review` after simplify to catch issues with severity-based reporting.
5. **Update Tracker** — Mark task done, update board, note any follow-up work or blockers discovered.

Steps 3 and 4 are mandatory before marking any task complete. Do not skip them.

**CRITICAL RULE: NEVER update the tracker or mark tasks done until `/simplify` and `/code-review` have been run and all fixes applied.** This has caught real bugs every single time — blocking SMTP calls, missing dispatch wiring, broken DB schemas, dead code paths, concurrency races. The review step is not optional overhead; it is where the hardest bugs are found.

When running multiple tracks in parallel via background agents, each agent must follow this full lifecycle independently. After all agents complete, run `/simplify` and `/code-review` on the combined output before updating the tracker. If you find yourself about to update the board without having run these steps, STOP and run them first.

### Parallelism Rules

- Always identify independent work that can happen simultaneously
- When multiple initiative tasks have no dependency between them, work them in parallel using background agents
- Within a single initiative, tasks are often sequential — respect the ordering unless explicitly independent
- Use worktree isolation when parallel streams touch overlapping files

### Environment Constraints

- **Local-first**: Everything runs on localhost via Docker Compose. No cloud deployments, no external services, no auth, no multi-tenancy.
- **Single `docker compose up`** should bring up the full stack: TimescaleDB, API server (with proxy), Dashboard.
- **Two capture modes**: (1) Transparent HTTP proxy on :8100 — forwards to upstream via `AGENTPROOF_UPSTREAM_URL`, captures traffic at HTTP level. (2) LiteLLM callback — installed directly on the proxy host. Proxy mode is primary; callback is the alternative.
- Port 8100 serves both `/v1/*` (proxy routes) and `/api/v1/*` (dashboard API). No route conflicts.
- Dashboard on localhost:8081, API+Proxy on localhost:8100, Postgres on localhost:5432.
- `make claude` launches Claude Code pointed at the proxy (`ANTHROPIC_BASE_URL=http://localhost:8100`).
- `make dev-proxy` optionally starts a local LiteLLM proxy on :4000 for testing the callback without an external proxy.

### Workflow Preferences

Prefer implementing changes over producing plan documents. If a task can be done in the current session, do it rather than writing a plan. Only produce plans when explicitly asked or when the scope genuinely requires user approval first.

### Architecture Decisions

Store architecture decisions in `.tracker/decisions/` as `ADR-NNN-<slug>.md`. Reference these from initiative files when relevant. Format:
- **Context** — why this decision was needed
- **Decision** — what we chose
- **Consequences** — tradeoffs accepted

---

## Debugging

When fixing bugs, test each fix in isolation before moving on. Do not chain multiple fixes together - verify one works before attempting the next.

## Development Environment

Always rebuild Docker containers after code changes before testing. Run `docker compose up --build` rather than assuming running containers have the latest code.

## Git Operations

When the user provides an explicit strategy (e.g., 'prioritize branch X for all conflicts'), follow it exactly. Do not override with your own judgment about which version is architecturally better.

---

## Codebase Patterns

### Package Structure

New features follow this package layout:
```
src/agentproof/<feature>/
  __init__.py        # Public exports
  types.py           # Pydantic models and enums
  <logic>.py         # Business logic (classifier, anomaly detection, etc.)
  writer.py          # If async DB writes needed — follows AsyncQueueWorker pattern
```

API routes go in `src/agentproof/api/routes/<feature>.py` and register in `app.py`.

### DB Schema Rules

- **Never modify existing schema files.** Create new `schema_<feature>.sql` files.
- TimescaleDB hypertables require composite primary keys: `PRIMARY KEY (id, created_at)`.
- Use hypertables for time-series data (events, benchmark results, alert history).
- Use regular tables for config/state data (alert rules, budget configs).
- Define continuous aggregates for queries that power the dashboard.
- Compression at 7 days, retention at 90 days (or 180 for benchmark data).

### Async Background Workers

`AsyncQueueWorker[T]` in `pipeline/base_worker.py` is the shared base class. Three workers extend it:
- `EventWriter(AsyncQueueWorker[LLMEvent])` — uses default `run()` loop, implements `_flush()` for COPY
- `MCPWriter(AsyncQueueWorker[MCPCall])` — overrides `run()` for dual queues (calls + edges)
- `BenchmarkWorker(AsyncQueueWorker[_BenchmarkItem])` — overrides `run()` for per-item API replay

Base class provides: pool management (`_ensure_pool`, `_close_pool`), `shutdown()` via `asyncio.Event`, `_flush_with_retry` with 3 attempts + individual fallback, and a default batch-drain-flush `run()` loop.
- Parent callback exposes `close(timeout)` that calls `shutdown()` + waits + cancels

### Shared Utilities

- `utils.py` has `utcnow()` — use instead of `datetime.now(timezone.utc)` for consistent timestamps and easy mocking
- `models.py` has `MODEL_CATALOG`, `ModelInfo`, `get_downgrade()`, `get_tier()` — single source of truth for model metadata

### Query Patterns

- Queries >= 24h use `daily_summary` continuous aggregate
- Hourly queries use `hourly_model_stats` aggregate
- Fall back to raw `llm_events` for: sub-hour ranges, org_id on hourly, per-event granularity (traces, waste analysis)
- `pg_interval` is always a bind parameter: `CAST(:bucket_interval AS INTERVAL)`
- Column names in GROUP BY come from validated allowlists — never from user input directly

### Config

- `AgentProofConfig` uses `pydantic-settings` with `AGENTPROOF_` env prefix
- Lazy initialization via `@lru_cache` on `get_config()` — avoids import-time side effects
- DB engine and session factory also lazy via `@lru_cache`
- Tests that need different config must call `get_config.cache_clear()`

### Types and Enums

- `LLMEvent` in `types.py` is the core data model — v1 frozen (add fields only, never remove/rename)
- `TaskType` enum is the canonical task taxonomy — used by classifier, waste scorer, benchmarking
- `EventStatus` enum for success/failure — prefer enum comparison over string literals
- New features should use existing enums. Don't create duplicate enums (e.g., `MCPCallStatus` duplicates `EventStatus` — known debt).

### Content Hashing

- `pipeline/hasher.py` provides `hash_content()` — SHA-256 with canonical JSON serialization
- Sort JSON keys, strip whitespace, fast-path skip for non-JSON strings
- Use for all content fingerprinting — prompts, completions, tool args, MCP params
- Never store raw user content, only hashes

### Classifier

- Rules-based classifier in `classifier/rules.py` — 86.6% accuracy on 82-example eval set
- `extract_keywords()` and `compute_token_ratio()` are shared utilities — use them, don't reimplement
- `TASK_KEYWORDS` dict is public (not underscore-prefixed) — the single source of keyword signals
- Classifier runs on the callback hot path — must stay sub-millisecond

### Waste Scoring

- `models.py` has `ModelInfo` dataclass and `MODEL_CATALOG` dict — single source of truth for model pricing, tiers, and downgrade paths
- `_suggest_model()` returns `(str | None, bool)` — None when not flagged
- `compute_waste_score()` takes raw DB rows, returns typed `WasteScoreResponse`

### Alerts

- Background `AlertChecker` runs on configurable interval with cooldown deduplication
- `dispatch_alert()` handles Slack (webhooks) and email (SMTP via `run_in_executor`)
- `_prune_fired()` prevents unbounded memory growth in cooldown tracker
- Alert rules stored in-memory for now — DB persistence is planned

### Benchmarking

- Traffic mirroring: `should_sample()` gates based on sample rate, status, task type
- Model replays run concurrently via `asyncio.gather` (not sequential)
- LLM-as-judge uses Haiku with task-specific rubrics
- Runtime config is mutable in-memory — not safe across multiple workers (planned: DB persistence)

### Testing

- Unit tests: `tests/unit/` — 210 tests, run with `pytest tests/unit/ -v`
- Integration tests: `tests/integration/` — use testcontainers with real TimescaleDB
- Framework tests: `tests/integration/frameworks/` — auto-skip if services/packages unavailable
- Shared test fixtures: `make_litellm_kwargs()`, `make_callback()`, `seed_events()`, `wait_for_flush()` in integration conftest
- Mock classes for LiteLLM responses are module-level (not recreated per call) for benchmark accuracy
- `setup_class` (not `setup_method`) for expensive deterministic computations
- After modifying seed data, classifier keywords, or routing logic, always run the full test suite (`pytest` or equivalent) before considering the task complete.

### Dashboard

- React 19 + Vite 6 + Tailwind 4 + Recharts + TanStack Query
- Dark theme, model colors via deterministic hash (not round-robin)
- Shared utilities in `dashboard/src/utils/format.ts`
- Chart data wrapped in `useMemo` to prevent unnecessary Recharts SVG re-renders
- `CardShell` component for consistent loading/error states across all cards

---

## Technical Debt Backlog

Tracked in `board.md` under "Technical Debt / Simplify Backlog". Address during hardening sprints, not during feature work.

---

## Project Tracking System

This project uses a lightweight markdown-based tracker in `.tracker/`. Treat it as the source of truth for project status.

### Structure

```
.tracker/
  board.md                        # Master board — high-level view of all initiatives
  decisions/                      # ADRs
  phases/
    phase-N/
      <id>-<slug>.md              # One file per initiative with tasks, owners, status
```

### How to Use the Tracker

**When the user asks about project status:**
- Read `.tracker/board.md` for the overview
- Read specific initiative files for task-level detail

**When the user asks to update a task or initiative:**
- Edit the specific initiative file (update checkbox, status, notes, blockers)
- Update `.tracker/board.md` to reflect any status changes
- Always update both files to keep them in sync

**When the user asks to add new work:**
- Create a new initiative file following the existing format
- Add a row to `board.md`

### Task Status Conventions

In initiative files, tasks use checkboxes:
- `- [ ]` — not started
- `- [~]` — in progress (add `**IN PROGRESS**` and owner name)
- `- [x]` — done
- `- [-]` — blocked (add `**BLOCKED:** reason`)

Initiative-level statuses in `board.md`:
- `not started` — no tasks begun
- `in progress` — at least one task active
- `blocked` — cannot proceed, note reason
- `done` — all tasks complete
- `cut` — descoped

### Conventions

- Owners are first names: `hj`, `TBD`, or team role placeholders (`infra`, `be1`, `be2`, `fe`, `ml`, `web3`)
- Dependencies reference initiative IDs like `0A`, `1C`
- Blockers go in the `## Blockers` section of each initiative file with a date
- When completing a task, add the date in `(done YYYY-MM-DD)` format
- Keep `board.md` and initiative files in sync — never update one without the other

### Quick Commands

Users may say things like:
- "show me the board" — read and display `board.md`
- "status on 1A" — read the specific initiative file
- "mark 0A-3 done" — check off that task, update board if initiative status changed
- "block 1B on 1A" — update blocker notes
- "add a task to 0C" — append a new task line to that initiative
- "what's next for [person]" — scan board for their assigned in-progress/not-started work
- "weekly standup" — summarize what's in progress, what's blocked, what finished recently
