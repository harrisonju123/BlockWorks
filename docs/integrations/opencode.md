# OpenCode Integration

Route OpenCode through AgentProof's LiteLLM proxy to capture every LLM call for observability, cost tracking, and task classification.

## Prerequisites

- Docker running locally
- AgentProof stack up: `docker compose up -d`
- Your LLM provider API key(s) set in `.env` (Anthropic, OpenAI, etc.)

Verify the stack is healthy:

```bash
curl -s http://localhost:8100/health | jq .
curl -s http://localhost:4000/health | jq .
```

## Configuration

OpenCode supports OpenAI-compatible endpoints natively. Point it at the LiteLLM proxy.

### opencode.json

Create or edit `opencode.json` in your project root (or `~/.config/opencode/config.json` for global config):

```json
{
  "provider": {
    "openai": {
      "apiKey": "sk-local-dev-key",
      "baseURL": "http://localhost:4000/v1"
    }
  },
  "model": {
    "default": {
      "provider": "openai",
      "model": "claude-sonnet"
    }
  }
}
```

The `apiKey` is the LiteLLM master key. Your real provider keys live server-side in `.env`.

### Environment variable alternative

```bash
export OPENAI_API_KEY=sk-local-dev-key
export OPENAI_BASE_URL=http://localhost:4000/v1
```

Then run OpenCode normally -- it will route through the proxy.

## Model Name Mapping

OpenCode sends the model name you configure. Map these to the aliases defined in `litellm-config.yaml`:

| OpenCode model config | LiteLLM alias | Routes to |
|---|---|---|
| `claude-opus` | `claude-opus` | `claude-opus-4-20250514` |
| `claude-sonnet` | `claude-sonnet` | `claude-sonnet-4-20250514` |
| `claude-haiku` | `claude-haiku` | `claude-haiku-4-5-20251001` |
| `gpt-4o` | `gpt-4o` | `gpt-4o` |
| `gpt-4o-mini` | `gpt-4o-mini` | `gpt-4o-mini` |
| `claude-sonnet-4-20250514` | `claude-sonnet-4-20250514` | `claude-sonnet-4-20250514` |

Use the alias names (left column) in your OpenCode config. If you need a model that isn't listed, add it to `litellm-config.yaml`.

## Verification

After running an OpenCode session through the proxy:

```bash
# Check captured events
curl -s http://localhost:8100/api/v1/events | jq '.count'

# CLI summary
agentproof stats

# Dashboard
open http://localhost:5173
```

## Example Session

```bash
# 1. Start the stack
docker compose up -d

# 2. Configure OpenCode (one-time)
cat > opencode.json << 'EOF'
{
  "provider": {
    "openai": {
      "apiKey": "sk-local-dev-key",
      "baseURL": "http://localhost:4000/v1"
    }
  },
  "model": {
    "default": {
      "provider": "openai",
      "model": "claude-sonnet"
    }
  }
}
EOF

# 3. Run OpenCode
opencode

# 4. After your session, check events
curl -s http://localhost:8100/api/v1/events | jq '.[0] | {model, prompt_tokens, completion_tokens, estimated_cost, task_type}'
```

## Troubleshooting

**Connection refused / timeout**

LiteLLM proxy isn't running on port 4000.

```bash
docker compose ps
docker compose logs litellm
curl http://localhost:4000/health
```

**"Unauthorized" or "Invalid API key"**

Set the API key to the LiteLLM master key (`sk-local-dev-key`), not your real provider key. The real key goes in `.env` on the server side.

**Model not found**

OpenCode sends whatever model name you configure. It must match an entry in `litellm-config.yaml`. Check available models:

```bash
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer sk-local-dev-key" | jq '.data[].id'
```

**Events not appearing**

1. Confirm the API: `curl http://localhost:8100/health`
2. Confirm the request actually went through LiteLLM (check its logs): `docker compose logs litellm --tail=20`
3. Check for callback errors: `docker compose logs litellm | grep -i "callback\|agentproof"`

**OpenCode using wrong base URL**

OpenCode resolves config from multiple sources. Make sure there isn't an `OPENAI_BASE_URL` env var overriding your `opencode.json`, or vice versa. Check with:

```bash
env | grep -i openai
```
