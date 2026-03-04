# AgentProof Demo Guide

5-minute walkthrough for a mixed audience (investors + engineers).

## Pre-Demo Checklist

| Step | Command | Verify |
|------|---------|--------|
| Stack running | `make demo` | Banner prints "Demo Ready" |
| Dashboard loads | Open http://localhost:8081 | Stats bar shows seeded data |
| API key exported | `echo $ANTHROPIC_API_KEY` | Non-empty |
| Terminal visible | Split screen: browser left, terminal right | Both visible to audience |

## Beat 1: Hook (30s)

**Page:** Overview (landing page)

> "Every AI-native company has invisible spend. We make it visible."

- Point at the **stats bar**: total requests, cost, tokens, error rate.
- Scroll briefly — model distribution chart, recent events.

| Audience | One-liner |
|----------|-----------|
| Investors | "This is real-time visibility into every dollar your AI agents spend." |
| Engineers | "Drop-in HTTP proxy — zero code changes, captures everything." |

## Beat 2: See (90s)

**Pages:** Events → Waste Details

1. **Events page** — click "Events" in sidebar.
   - Filter by status = `error` to show failure spikes.
   - Point out model, task type, latency columns.
   - "Every LLM call is captured: prompt hash, tokens, cost, latency."

2. **Waste Details** — click "Waste" in sidebar.
   - Show the **total savings** number at the top.
   - Scroll the per-task breakdown: "Sonnet used for classification — Haiku handles this at 1/10th the cost."
   - Point at a flagged row and show the suggested downgrade.

| Audience | One-liner |
|----------|-----------|
| Investors | "We found $X/month in waste across 8 task types — automatically." |
| Engineers | "Rules-based classifier tags every call, waste scorer suggests cheaper models." |

## Beat 3: Optimize (90s)

**Pages:** Routing → Benchmarks

1. **Routing page** — click "Routing" in sidebar.
   - Show the routing policy table: task type → model mapping.
   - Point at the decision feed: "Every routing decision is logged with the reason."
   - "Once you trust the waste analysis, flip these into routing rules."

2. **Benchmarks page** — click "Benchmarks" in sidebar.
   - Show the fitness matrix: task type × model grid with quality scores.
   - "Before we route, we prove the cheaper model works. LLM-as-judge scores every downgrade."
   - Point at a cell: "Haiku scores 4.2/5 on classification — that's why we route it there."

| Audience | One-liner |
|----------|-----------|
| Investors | "We don't just find waste — we prove the fix works before applying it." |
| Engineers | "Traffic mirroring replays calls to candidate models; LLM judge scores quality." |

## Beat 4: Trust (90s)

**Pages:** Attestations + Live Traffic

1. **Attestations page** — click "Attestations" in sidebar.
   - Show a recent attestation: content hash, block number, transaction hash.
   - "Every classification and routing decision is hashed on-chain. Immutable audit trail."

2. **Live traffic** — switch to terminal.
   - Run: `make demo-traffic`
   - Switch back to Events page in the dashboard.
   - Watch new rows appear in real time as each request flows through.
   - "One proxy, zero code changes. That's all it takes."

| Audience | One-liner |
|----------|-----------|
| Investors | "On-chain attestations give customers a compliance audit trail they can verify." |
| Engineers | "SHA-256 content hash → EVM transaction. The chain is the receipt." |

## Closing

> "AgentProof sits between your agents and the LLM. One line of config gives you visibility, cost optimization, quality assurance, and a verifiable audit trail."

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `make demo` fails with "Docker daemon not running" | Start Docker Desktop, wait for it to initialize |
| Dashboard blank / loading forever | Wait 30s for Vite cold start, or hard refresh |
| `demo-traffic` errors on all requests | Check `ANTHROPIC_API_KEY` is exported and valid |
| OpenAI requests skipped | Expected without LiteLLM — run `make dev-proxy` first |
| Events not appearing in dashboard | Refresh the page; check API logs with `make logs` |
| Stale data from previous demo | Run `make demo-reset && make demo` for clean slate |
