# LangChain Integration

Route LangChain LLM calls through AgentProof's LiteLLM proxy to capture every request for observability, cost tracking, and task classification.

## Prerequisites

- Docker running locally
- AgentProof stack up: `docker compose up -d`
- Your LLM provider API key(s) set in `.env` (Anthropic, OpenAI, etc.)
- LangChain installed:

```bash
pip install langchain langchain-openai
```

Verify the stack is healthy:

```bash
curl -s http://localhost:8100/health | jq .
curl -s http://localhost:4000/health | jq .
```

## Approach 1: Proxy (recommended)

Point LangChain's `ChatOpenAI` at the LiteLLM proxy. No code changes to your chains — just swap the base URL.

LiteLLM exposes an OpenAI-compatible API, so `ChatOpenAI` works for both OpenAI and Anthropic models. LiteLLM handles the translation to the upstream provider.

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-local-dev-key",
    model="claude-sonnet",
)

response = llm.invoke("What is AgentProof?")
print(response.content)
```

The `api_key` is the LiteLLM master key, not your real provider key. Your actual Anthropic/OpenAI keys live server-side in `.env`.

### Anthropic models through the proxy

Use `ChatOpenAI` pointed at the proxy — **not** `ChatAnthropic`. The `ChatAnthropic` class uses the native Anthropic API format and won't route through the OpenAI-compatible proxy correctly. LiteLLM handles the translation from OpenAI format to Anthropic's API internally.

```python
# Correct — works for any model the proxy knows about
llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-local-dev-key",
    model="claude-sonnet",       # or "claude-haiku", "gpt-4o", etc.
)

# Wrong — bypasses the proxy
# from langchain_anthropic import ChatAnthropic
# llm = ChatAnthropic(model="claude-sonnet-4-20250514")
```

## Approach 2: LiteLLM SDK callback

Use LiteLLM's Python SDK as a drop-in replacement for direct provider calls. LiteLLM automatically triggers the AgentProof callback.

```python
from litellm import completion

response = completion(
    model="claude-sonnet",
    messages=[{"role": "user", "content": "What is AgentProof?"}],
    api_base="http://localhost:4000/v1",
    api_key="sk-local-dev-key",
)

print(response.choices[0].message.content)
```

This approach is useful when you have custom code that calls models directly rather than through LangChain chains.

## Passing Trace Metadata

AgentProof extracts trace context from LiteLLM metadata. To correlate events with specific sessions or traces, pass metadata through the `model_kwargs` parameter:

```python
llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-local-dev-key",
    model="claude-sonnet",
    model_kwargs={
        "extra_headers": {
            "x-session-id": "my-session-123",
        },
        "metadata": {
            "session_id": "my-session-123",
            "trace_id": "trace-abc-def",
            "agent_framework": "langchain",
            "agent_name": "my-summarizer",
        },
    },
)
```

The metadata fields that AgentProof recognizes:

| Field | Purpose |
|---|---|
| `session_id` | Groups events from the same user session |
| `trace_id` | Links events within a single chain execution |
| `parent_span_id` | Connects child calls to their parent |
| `agent_framework` | Overrides auto-detection (e.g. `"langchain"`) |
| `agent_name` | Human-readable name for the agent |

## Model Name Mapping

Use the aliases defined in `litellm-config.example.yaml`:

| LangChain model param | LiteLLM alias | Routes to |
|---|---|---|
| `claude-opus` | `claude-opus` | `claude-opus-4-20250514` |
| `claude-sonnet` | `claude-sonnet` | `claude-sonnet-4-20250514` |
| `claude-haiku` | `claude-haiku` | `claude-haiku-4-5-20251001` |
| `gpt-4o` | `gpt-4o` | `gpt-4o` |
| `gpt-4o-mini` | `gpt-4o-mini` | `gpt-4o-mini` |

If the model name doesn't match any alias, the proxy returns a "model not found" error. Check available models:

```bash
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer sk-local-dev-key" | jq '.data[].id'
```

## Verification

After running a LangChain chain through the proxy:

```bash
# Check captured events
curl -s http://localhost:8100/api/v1/events | jq '.total_count'

# CLI summary
agentproof stats

# Dashboard
open http://localhost:8081
```

## Example: Chain with Output Parser

A complete example showing a prompt template, LLM call, and output parser running through the proxy:

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 1. Point at the proxy
llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-local-dev-key",
    model="claude-sonnet",
)

# 2. Build a chain
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a concise technical writer."),
    ("user", "Summarize this concept in one sentence: {topic}"),
])

chain = prompt | llm | StrOutputParser()

# 3. Run it
result = chain.invoke({"topic": "observability for AI agents"})
print(result)

# 4. Verify capture
import httpx
resp = httpx.get("http://localhost:8100/api/v1/events", params={"limit": 1})
event = resp.json()["events"][0]
print(f"Model: {event['model']}, Tokens: {event['total_tokens']}, Cost: ${event['estimated_cost']:.4f}")
```

## Example: Tool Calling

LangChain tool calls also flow through the proxy and are captured by AgentProof:

```python
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"Sunny, 72F in {city}"

llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-local-dev-key",
    model="claude-sonnet",
).bind_tools([get_weather])

response = llm.invoke("What's the weather in San Francisco?")
print(response.tool_calls)
```

AgentProof captures the tool call names and argument hashes in the event record.

## Troubleshooting

**Connection refused / timeout**

LiteLLM proxy isn't running on port 4000.

```bash
docker compose ps
docker compose logs litellm
curl http://localhost:4000/health
```

**"Unauthorized" or "Invalid API key"**

Set the `api_key` to the LiteLLM master key (`sk-local-dev-key`), not your real provider key. The real key goes in `.env` on the server side.

**Model not found**

The model name in `ChatOpenAI(model=...)` must match an alias in `litellm-config.example.yaml`. Check what's available:

```bash
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer sk-local-dev-key" | jq '.data[].id'
```

**Using ChatAnthropic instead of ChatOpenAI**

`ChatAnthropic` sends requests in Anthropic's native format directly, bypassing the OpenAI-compatible proxy. Switch to `ChatOpenAI` pointed at the proxy — it works for Anthropic models too, since LiteLLM translates the format.

**Events not appearing**

1. Confirm the API: `curl http://localhost:8100/health`
2. Confirm the request went through LiteLLM: `docker compose logs litellm --tail=20`
3. Check for callback errors: `docker compose logs litellm | grep -i "callback\|agentproof"`

**LangChain streaming not working**

Streaming works through the proxy. If you hit issues, check that you're using `ChatOpenAI` (not `ChatAnthropic`) and that LiteLLM's `drop_params: true` is set in `litellm-config.example.yaml`.
