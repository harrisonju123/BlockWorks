# CrewAI Integration

Route CrewAI agent LLM calls through AgentProof's LiteLLM proxy to capture every request for observability, cost tracking, and task classification.

CrewAI uses LiteLLM under the hood for model routing, which makes integration straightforward — set two environment variables and every agent call flows through the proxy automatically.

## Prerequisites

- Docker running locally
- AgentProof stack up: `docker compose up -d`
- Your LLM provider API key(s) set in `.env` (Anthropic, OpenAI, etc.)
- CrewAI installed:

```bash
pip install crewai
```

Verify the stack is healthy:

```bash
curl -s http://localhost:8100/health | jq .
curl -s http://localhost:4000/health | jq .
```

## Configuration

Set these environment variables before running your CrewAI scripts:

```bash
export OPENAI_API_BASE=http://localhost:4000/v1
export OPENAI_API_KEY=sk-local-dev-key
```

The `OPENAI_API_KEY` is the LiteLLM master key, not your real OpenAI key. Your actual provider keys live server-side in `.env`.

That's it. CrewAI's internal LiteLLM client picks up these environment variables and routes all LLM calls through the proxy.

### .env file alternative

Add to the `.env` file in your project directory:

```bash
OPENAI_API_BASE=http://localhost:4000/v1
OPENAI_API_KEY=sk-local-dev-key
```

Then load it before running:

```bash
source .env
python my_crew.py
```

## Agent Definition Example

A minimal CrewAI setup where all LLM calls are automatically captured by AgentProof:

```python
import os
from crewai import Agent, Task, Crew

# Ensure proxy routing is configured
os.environ["OPENAI_API_BASE"] = "http://localhost:4000/v1"
os.environ["OPENAI_API_KEY"] = "sk-local-dev-key"

# Define an agent — uses the proxy automatically
researcher = Agent(
    role="Senior Researcher",
    goal="Find and summarize key information about a topic",
    backstory="You are an experienced researcher with attention to detail.",
    llm="claude-sonnet",     # matches the alias in litellm-config.example.yaml
    verbose=True,
)

# Define a task
research_task = Task(
    description="Research the current state of AI agent observability tools.",
    expected_output="A concise summary with key findings.",
    agent=researcher,
)

# Run the crew
crew = Crew(
    agents=[researcher],
    tasks=[research_task],
    verbose=True,
)

result = crew.kickoff()
print(result)
```

## Multi-Agent Workflows

CrewAI's multi-agent workflows generate multiple LLM calls per task — one for each agent interaction, planning step, and delegation. AgentProof captures each call individually with trace context, so you can see the full breakdown:

```python
researcher = Agent(
    role="Researcher",
    goal="Gather raw information",
    backstory="You find reliable sources quickly.",
    llm="claude-sonnet",
    verbose=True,
)

writer = Agent(
    role="Writer",
    goal="Turn research into polished content",
    backstory="You write clear, engaging technical content.",
    llm="claude-sonnet",
    verbose=True,
)

research_task = Task(
    description="Research AI agent observability.",
    expected_output="Key facts and findings.",
    agent=researcher,
)

writing_task = Task(
    description="Write a summary based on the research.",
    expected_output="A well-structured 200-word summary.",
    agent=writer,
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    verbose=True,
)

result = crew.kickoff()
```

After this run, check the dashboard — you'll see separate events for each agent's LLM calls, with token counts and costs broken down per call.

## Model Name Mapping

Use the aliases from `litellm-config.example.yaml` in your agent's `llm` parameter:

| CrewAI `llm` param | LiteLLM alias | Routes to |
|---|---|---|
| `claude-opus` | `claude-opus` | `claude-opus-4-20250514` |
| `claude-sonnet` | `claude-sonnet` | `claude-sonnet-4-20250514` |
| `claude-haiku` | `claude-haiku` | `claude-haiku-4-5-20251001` |
| `gpt-4o` | `gpt-4o` | `gpt-4o` |
| `gpt-4o-mini` | `gpt-4o-mini` | `gpt-4o-mini` |

CrewAI passes the `llm` string directly to LiteLLM, so it must match an alias the proxy recognizes.

## Verification

After running a CrewAI workflow through the proxy:

```bash
# Check captured events
curl -s http://localhost:8100/api/v1/events | jq '.total_count'

# CLI summary
agentproof stats

# Dashboard
open http://localhost:8081
```

A single CrewAI task typically generates 2-5+ LLM calls (planning, execution, validation), so expect more events than the number of tasks.

## Example Session

```bash
# 1. Start the stack
docker compose up -d

# 2. Set proxy env vars
export OPENAI_API_BASE=http://localhost:4000/v1
export OPENAI_API_KEY=sk-local-dev-key

# 3. Run your crew
python my_crew.py

# 4. Check captured events
curl -s http://localhost:8100/api/v1/events?limit=10 | jq '.events[] | {model, prompt_tokens, completion_tokens, estimated_cost, task_type}'
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

`OPENAI_API_KEY` must be set to the LiteLLM master key (`sk-local-dev-key`). Your real provider key goes in `.env` on the server side.

**Model not found / "model does not exist"**

The model name in `Agent(llm=...)` must match an alias in `litellm-config.example.yaml`. CrewAI sometimes prefixes model names with a provider (e.g. `openai/gpt-4o`). If you see this, either add the prefixed name to `litellm-config.example.yaml` or use the bare alias.

Check available models:

```bash
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer sk-local-dev-key" | jq '.data[].id'
```

**Agent name not detected**

AgentProof attempts to auto-detect agent names from request metadata. CrewAI doesn't always set identifiable headers. If you need per-agent attribution, you can pass metadata explicitly by configuring the LiteLLM SDK in your CrewAI setup.

**Events not appearing**

1. Confirm the API: `curl http://localhost:8100/health`
2. Confirm the env vars are set: `echo $OPENAI_API_BASE` should print `http://localhost:4000/v1`
3. Confirm requests route through LiteLLM: `docker compose logs litellm --tail=20`
4. Check for callback errors: `docker compose logs litellm | grep -i "callback\|agentproof"`

**CrewAI using wrong base URL**

CrewAI reads from multiple sources. Make sure there isn't a conflicting `OPENAI_BASE_URL` or `LITELLM_API_BASE` set. Check with:

```bash
env | grep -iE "openai|litellm"
```

**High token usage**

CrewAI's internal planning and validation steps add LLM calls beyond your explicit tasks. This is expected. Use the AgentProof dashboard to see the full breakdown and identify which steps consume the most tokens.
