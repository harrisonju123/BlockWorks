# Claude Code Integration

Route Claude Code through AgentProof's LiteLLM proxy to capture every LLM call for observability, cost tracking, and task classification.

## Prerequisites

- Docker running locally
- AgentProof stack up: `docker compose up -d`
- `ANTHROPIC_API_KEY` exported in the shell where you'll run Claude Code

Verify the stack is healthy before proceeding:

```bash
curl -s http://localhost:8100/health | jq .
curl -s http://localhost:4000/health | jq .
```

## Configuration

Claude Code supports custom API endpoints via environment variables. Point it at the LiteLLM proxy instead of Anthropic's API directly.

### Option 1: Environment variables (recommended)

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_API_KEY=sk-local-dev-key
```

The `ANTHROPIC_API_KEY` here is the LiteLLM master key, not your real Anthropic key. Your actual Anthropic key is read by LiteLLM from the server-side environment (set in `.env` or `docker-compose.yml`).

Then launch Claude Code normally:

```bash
claude
```

### Option 2: Per-invocation

```bash
ANTHROPIC_BASE_URL=http://localhost:4000 ANTHROPIC_API_KEY=sk-local-dev-key claude
```

### Option 3: Claude Code settings file

Add to `~/.claude/settings.json`:

```json
{
  "apiBaseUrl": "http://localhost:4000"
}
```

When using this method you still need the `ANTHROPIC_API_KEY=sk-local-dev-key` environment variable set, since the settings file only covers the base URL.

## Model Name Mapping

LiteLLM translates between alias names and actual model IDs. The proxy is configured with these aliases in `litellm-config.yaml`:

| You request (Claude Code default) | LiteLLM routes to |
|---|---|
| `claude-opus` | `claude-opus-4-20250514` |
| `claude-sonnet` | `claude-sonnet-4-20250514` |
| `claude-haiku` | `claude-haiku-4-5-20251001` |

Claude Code sends model names like `claude-sonnet-4-20250514` by default. If the proxy doesn't recognize the exact model name, add it as an alias in `litellm-config.yaml` or use the `model_name` field that matches what Claude Code sends. See the `litellm-config.yaml` comments for details.

## Verification

After running a Claude Code session through the proxy, confirm events were captured:

```bash
# Check that the API has recorded events
curl -s http://localhost:8100/api/v1/events | jq '.count'

# Or use the CLI
agentproof stats

# Check the dashboard
open http://localhost:5173
```

You should see events with `provider: "anthropic"` and the model name you used.

## Example Session

```bash
# 1. Start the stack
docker compose up -d

# 2. Export proxy config
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_API_KEY=sk-local-dev-key

# 3. Run Claude Code
claude "explain what this project does" --print

# 4. Check captured events
curl -s http://localhost:8100/api/v1/events | jq '.[0] | {model, prompt_tokens, completion_tokens, estimated_cost, task_type}'
```

Expected output:

```json
{
  "model": "claude-sonnet-4-20250514",
  "prompt_tokens": 1847,
  "completion_tokens": 312,
  "estimated_cost": 0.0094,
  "task_type": "explain"
}
```

## Troubleshooting

**Claude Code returns connection errors**

The LiteLLM proxy isn't running or isn't reachable on port 4000.

```bash
docker compose ps          # check litellm container status
docker compose logs litellm  # check for startup errors
curl http://localhost:4000/health
```

**"Invalid API key" from Claude Code**

When routing through the proxy, set `ANTHROPIC_API_KEY` to the LiteLLM master key (`sk-local-dev-key`), not your actual Anthropic key. Your real key goes in the server-side `.env` file.

**Model not found / "model does not exist"**

Claude Code may request a model name that doesn't match any alias in `litellm-config.yaml`. Check what model Claude Code is requesting (look at the LiteLLM logs) and add a matching entry:

```bash
docker compose logs litellm | grep "model"
```

Then add the exact model name to `litellm-config.yaml` under `model_list`.

**Events not showing up in the API**

1. Confirm the API is running: `curl http://localhost:8100/health`
2. Check LiteLLM callback config: the `litellm_settings.callbacks` field must list the AgentProof callback
3. Check LiteLLM logs for callback errors: `docker compose logs litellm | grep -i "callback\|error"`

**High latency**

The proxy adds minimal overhead (<10ms). If you see significant latency, check that Docker has enough resources allocated (CPU/memory) and that the DB isn't overloaded.
