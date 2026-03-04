# BlockWorks

AI agent observability, benchmarking, and on-chain attestation platform. Two deployment modes: **transparent HTTP proxy** (recommended — zero config on your LLM provider) or **LiteLLM callback** (if you control the proxy host). Every LLM request is captured for cost analysis, waste detection, quality benchmarking, and cryptographic attestation.

## Architecture

### Proxy Mode (recommended)

AgentProof sits between your agent and the upstream LLM provider, capturing all traffic transparently. An embedded Anvil node provides on-chain attestation.

```
┌─────────────┐     ┌───────────────────┐     ┌───────────────┐
│  AI Agents  │────▶│  AgentProof :8100  │────▶│  LLM Provider │
│ (Claude     │     │  (proxy + capture  │     │  (Anthropic,  │
│  Code, etc.)│     │   + smart routing) │     │   OpenAI, etc)│
└─────────────┘     └──┬─────────┬──────┘     └───────────────┘
                       │         │ async queues
                 ┌─────▼───┐  ┌──▼──────────┐
                 │ Event   │  │ Benchmark   │
                 │ Writer  │  │ Worker      │
                 └─────┬───┘  └──┬──────────┘
            ┌──────────┼─────────┘
      ┌─────▼─────┐  ┌─────▼─────┐  ┌──────────┐
      │TimescaleDB│  │ Dashboard  │  │  Anvil   │
      │ :5432     │  │ :8081      │  │  :8545   │
      └───────────┘  └───────────┘  └──────────┘
```

### Callback Mode (alternative)

If you control the LiteLLM proxy host, install the callback directly.

```
┌─────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  AI Agents  │────▶│  Your LiteLLM Proxy  │────▶│ LLM Provider │
│ (Claude,    │     │                      │     │ (Anthropic,  │
│  GPT, etc.) │     │  + AgentProofCallback │     │  OpenAI)     │
└─────────────┘     └──────────┬───────────┘     └──────────────┘
                               │ async queue
                         ┌─────▼──────┐
                         │ EventWriter│
                         │ (batch     │
                         │  COPY)     │
                         └─────┬──────┘
                    ┌──────────┼──────────┐
              ┌─────▼─────┐  ┌─────▼─────┐
              │TimescaleDB│  │ Dashboard  │
              │ :5432     │  │ :8081      │
              └───────────┘  │ + API :8100│
                             └───────────┘
```

## What It Does

**Observability** — Every LLM call is captured with token counts, latency, cost, content hashes (never raw content), trace context, and tool call details. The rules-based classifier tags each call by task type (code generation, classification, summarization, etc.).

**Waste Detection** — Identifies overspend by matching task complexity to model tier. A simple classification task hitting Opus gets flagged with a cheaper alternative and projected savings.

**Benchmarking** — Mirrors sampled production traffic to alternative models, runs LLM-as-judge quality scoring with task-specific rubrics, and produces cost/quality tradeoff data.

**Smart Routing** — YAML policy DSL that routes requests based on task type, model fitness scores, budget constraints, and A/B testing rules.

**MCP Tracing** — Captures Model Context Protocol server calls, builds execution DAGs, and detects wasted tool invocations.

**Alerts & Budgets** — Z-score anomaly detection against rolling baselines. Budget caps with configurable actions (alert, downgrade, block). Slack webhook and email dispatch.

**On-Chain Attestation** — Cryptographic proof of AI usage via Merkle trees and Solidity contracts. Billing verification, compliance audit trails, and state channels for high-frequency attestation batching.

**Decentralized Validation** — Multi-validator consensus with stake-weighted voting. Trust scores for agents based on attestation history.

**Token & Governance** — ERC-20 token with proposal/vote governance engine for protocol parameters.

**Marketplace** — Agent/MCP registry, composable workflow builder, revenue sharing protocol, enterprise multi-tenancy with RBAC, and cross-platform interoperability.

## Quick Start

### Prerequisites

- Docker & Docker Compose

### Option A: Proxy Mode (recommended)

No access to your LLM proxy host required. Point your agents at AgentProof and set the upstream URL.

```bash
git clone https://github.com/harrisonju123/BlockWorks.git
cd BlockWorks

# Set your upstream LLM provider
export AGENTPROOF_UPSTREAM_URL=https://your-litellm-proxy.example.com

# Start DB + API (with proxy) + Dashboard
make dev

# Open the dashboard
open http://localhost:8081
```

Now point your agent at AgentProof:

```bash
# Claude Code
ANTHROPIC_BASE_URL=http://localhost:8100 claude

# Or use the make target
make claude

# Any OpenAI-compatible client
export OPENAI_BASE_URL=http://localhost:8100/v1
```

Every LLM call is transparently proxied to your upstream provider and captured for analysis.

### Option B: Callback Mode

If you control the LiteLLM proxy host, install the callback directly:

```yaml
litellm_settings:
  callbacks: ["agentproof.pipeline.callback.AgentProofCallback"]
```

```bash
pip install agentproof
export AGENTPROOF_DATABASE_URL=postgresql+asyncpg://agentproof:localdev@localhost:5432/agentproof
```

See `litellm-config.example.yaml` for a full reference config. Then start the stack:

```bash
make dev
open http://localhost:8081
```

### Resetting

To wipe the DB and start fresh (re-applies all schemas):

```bash
make reset
```

### Local development (optional)

For testing the callback locally without an external proxy, `make dev-proxy` starts a local LiteLLM proxy on port 4000 with the callback pre-configured:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
make dev-proxy
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| API Server | 8100 | REST API (`/api/v1/*`) + transparent proxy (`/v1/*`) |
| Dashboard | 8081 | React UI — spend, traces, waste, routing, benchmarks, attestations |
| TimescaleDB | 5432 | Time-series storage with continuous aggregates |
| Anvil | 8545 | Local EVM node for on-chain attestation (auto-deployed contracts) |

## Project Structure

```
src/agentproof/
  pipeline/         # LiteLLM callback, async event writer, base worker class
  classifier/       # Rules-based task classification (code gen, summarization, etc.)
  db/               # Shared query helpers (events, stats, aggregates)
  api/              # FastAPI REST endpoints (~100 across 20 route modules)
  cli/              # Typer CLI (stats, waste-report, evaluate)
  benchmarking/     # Traffic mirroring, LLM-as-judge, model comparison
  mcp/              # MCP server call tracing and execution DAGs
  alerts/           # Anomaly detection, budget caps, Slack/email dispatch
  waste/            # Waste scoring — model overkill, context bloat detection
  routing/          # YAML policy DSL, fitness-based model selection, A/B testing
  attestation/      # Merkle trees, EVM provider, scheduled on-chain proof submission
  billing/          # Invoice parsing, cost reconciliation, attestation-backed billing
  compliance/       # Audit trail export, framework mapping (SOC2, ISO 27001)
  channels/         # State channels for batched high-frequency attestations
  validators/       # Decentralized multi-validator consensus
  governance/       # Proposal/vote engine for protocol parameters
  trust/            # Agent trust scores from attestation history
  sdk/              # Python SDK with @track_llm_call decorator
  fitness/          # Global model fitness index and trend analysis
  registry/         # Agent and MCP server registry with discovery
  enterprise/       # Multi-tenancy, RBAC, tenant isolation
  workflows/        # Composable workflow builder (DAG execution engine)
  revenue/          # Revenue sharing protocol for registered agents
  interop/          # Cross-platform message protocol with HMAC signing
  models.py         # Unified model catalog (pricing, tiers, downgrade paths)
  types.py          # Core LLMEvent model, TaskType enum, EventStatus enum
  config.py         # Pydantic-settings config with AGENTPROOF_ env prefix
  utils.py          # Shared helpers (utcnow)

contracts/src/      # Solidity 0.8.24 (Foundry)
  AgentProofAttestation.sol   # Batch attestation with chain linkage
  AgentProofChannel.sol       # State channels for high-freq batching
  AgentProofStaking.sol       # Validator stake management
  AgentProofToken.sol         # ERC-20 governance token
  AgentProofTrust.sol         # On-chain trust score registry
  AgentProofRevenue.sol       # Revenue distribution splits

dashboard/          # React 19 + Vite 6 + Tailwind 4 + Recharts
```

## API

The API server exposes ~100 endpoints under `/api/v1/`. Key groups:

```
GET  /api/v1/stats/summary          # Spend, latency, call counts over time
GET  /api/v1/stats/top-traces       # Highest-cost trace chains
GET  /api/v1/stats/waste            # Waste score with per-model breakdown
GET  /api/v1/benchmarks/compare     # Model quality/cost comparison
GET  /api/v1/mcp/stats              # MCP server health and latency
GET  /api/v1/mcp/graph/{trace_id}   # Execution DAG for a trace
POST /api/v1/alerts/rules           # Create alert rules
GET  /api/v1/routing/policy         # Current routing policy
POST /api/v1/attestation/submit     # Submit attestation batch on-chain
GET  /api/v1/attestation/latest     # Most recent attestation + chain integrity
GET  /api/v1/attestation/verify     # Verify a specific attestation hash
GET  /api/v1/fitness/index          # Global model fitness rankings
GET  /api/v1/registry/agents        # Registered agent catalog
```

## CLI

```bash
agentproof stats              # Spend summary, top traces, waste score
agentproof stats --period 7d  # Last 7 days
agentproof waste-report       # Detailed waste analysis
agentproof evaluate           # Run classifier accuracy eval
```

## Testing

```bash
make test-unit         # ~1460 unit tests (~3s)
make test-integration  # Integration tests with real TimescaleDB (testcontainers)
make test              # All tests with coverage
make lint              # Ruff linting
make typecheck         # mypy strict mode
make ci                # Full CI: lint + typecheck + test
make forge-test        # Solidity contract tests (Foundry)
make deploy-local      # Deploy contracts to local Anvil
```

## How the Pipeline Works

### Proxy mode

1. Agent sends LLM request to AgentProof `:8100/v1/*`
2. The proxy forwards to the upstream provider via `AGENTPROOF_UPSTREAM_URL`
3. On response, the proxy (non-blocking):
   - Hashes prompt/completion content (SHA-256, never stores raw text)
   - Classifies the task type via rules-based classifier
   - Evaluates the smart routing policy (fitness scores, budget, A/B rules)
   - Detects MCP server calls from tool_use blocks
   - Queues an `LLMEvent` to the `EventWriter`
   - Optionally samples traffic to `BenchmarkWorker` for cross-model comparison
4. `EventWriter` batches events and flushes to TimescaleDB via `COPY`
5. `AttestationScheduler` periodically builds Merkle trees and submits on-chain proofs
6. TimescaleDB continuous aggregates pre-compute hourly and daily rollups
7. Dashboard and API read from aggregates for fast queries

### Callback mode

Same pipeline, but the `AgentProofCallback` is installed directly on a LiteLLM proxy host instead of using the transparent proxy.

## Configuration

All config uses environment variables with the `AGENTPROOF_` prefix:

```bash
AGENTPROOF_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agentproof
AGENTPROOF_UPSTREAM_URL=http://localhost:4000    # upstream LLM provider for proxy mode
AGENTPROOF_ENV=development
AGENTPROOF_API_HOST=0.0.0.0
AGENTPROOF_API_PORT=8100
AGENTPROOF_BENCHMARK_SAMPLE_RATE=0.1
AGENTPROOF_ALERT_CHECK_INTERVAL_S=60
AGENTPROOF_ATTESTATION_RPC_URL=http://localhost:8545  # Anvil / EVM RPC for attestations
AGENTPROOF_ATTESTATION_INTERVAL_S=300                 # Merkle tree submission frequency
```

## License

Private. All rights reserved.
