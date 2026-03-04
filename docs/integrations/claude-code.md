# Claude Code Integration

Claude Code already routes through your LiteLLM proxy. AgentProof hooks in as a callback on that proxy — no changes needed on the Claude Code side.

## Prerequisites

- Your LiteLLM proxy running with `AgentProofCallback` installed (see root README)
- AgentProof stack up: `make dev` (DB + API + Dashboard)

Verify the stack is healthy:

```bash
curl -s http://localhost:8100/health | jq .
```

## How It Works

Claude Code's `ANTHROPIC_BASE_URL` already points at your LiteLLM proxy. Once the `AgentProofCallback` is registered in that proxy's config, every LLM call is automatically captured — classified, hashed, and written to TimescaleDB.

No additional Claude Code configuration needed.

## Model Name Mapping

LiteLLM translates between alias names and actual model IDs. Configure these in your LiteLLM proxy config (see `litellm-config.example.yaml` for reference):

| You request (Claude Code default) | LiteLLM routes to |
|---|---|
| `claude-opus` | `claude-opus-4-20250514` |
| `claude-sonnet` | `claude-sonnet-4-20250514` |
| `claude-haiku` | `claude-haiku-4-5-20251001` |

Claude Code sends model names like `claude-sonnet-4-20250514` by default. If the proxy doesn't recognize the exact model name, add it as an alias in your proxy config.

## Verification

After running a Claude Code session, confirm events were captured:

```bash
# Check that the API has recorded events
curl -s http://localhost:8100/api/v1/events | jq '.count'

# Or use the CLI
agentproof stats

# Check the dashboard
open http://localhost:8081
```

You should see events with `provider: "anthropic"` and the model name you used.

## Troubleshooting

**Events not showing up in the API**

1. Confirm the API is running: `curl http://localhost:8100/health`
2. Check your LiteLLM proxy config: `litellm_settings.callbacks` must list `agentproof.pipeline.callback.AgentProofCallback`
3. Check that `AGENTPROOF_DATABASE_URL` is set in the proxy's environment
4. Check proxy logs for callback errors

**Model not found / "model does not exist"**

Claude Code may request a model name that doesn't match any alias in your proxy config. Check what model Claude Code is requesting (look at the LiteLLM logs) and add a matching entry.
