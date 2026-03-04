#!/usr/bin/env python3
"""Send live demo traffic through the AgentProof proxy.

Sends 8-10 real requests through :8100 so judges can see live data
flowing into the dashboard. Requires ANTHROPIC_API_KEY in env.

Usage:
    python scripts/demo_traffic.py
    # or:
    make demo-traffic
"""

from __future__ import annotations

import os
import sys
import time

import httpx

PROXY_URL = os.environ.get("AGENTPROOF_PROXY_URL", "http://localhost:8100")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "") or API_KEY

def _check_api_key() -> None:
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set. Export it and try again.")
        sys.exit(1)

HEADERS = {
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

# Each request: (description, body) — model is derived from body["model"]
REQUESTS: list[tuple[str, dict]] = [
    (
        "Code generation (Sonnet)",
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": "Write a Python function that checks if a string is a valid email address using regex."}],
        },
    ),
    (
        "Summarization (Haiku)",
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 200,
            "messages": [{"role": "user", "content": "Summarize in 2 sentences: Machine learning is a subset of artificial intelligence that enables systems to learn from data and improve over time without being explicitly programmed. It uses algorithms to identify patterns in data, making predictions or decisions based on new inputs."}],
        },
    ),
    (
        "Classification (Haiku)",
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "Classify the sentiment of this review as positive, negative, or neutral: 'The product works fine but the shipping was slower than expected.'"}],
        },
    ),
    (
        "Reasoning (Sonnet)",
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": "A farmer has 17 sheep. All but 9 die. How many sheep does the farmer have left? Explain your reasoning step by step."}],
        },
    ),
    (
        "Tool use (Sonnet)",
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get the current weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City and state"},
                        },
                        "required": ["location"],
                    },
                }
            ],
            "messages": [{"role": "user", "content": "What's the weather like in San Francisco?"}],
        },
    ),
    (
        "Code review (Sonnet)",
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "Review this Python code for bugs:\n\ndef divide_list(numbers):\n    results = []\n    for n in numbers:\n        results.append(100 / n)\n    return results"}],
        },
    ),
    (
        "Extraction (Haiku)",
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 150,
            "messages": [{"role": "user", "content": "Extract the name, email, and phone from this text: 'Hi, I'm Jane Smith. You can reach me at jane.smith@example.com or call 555-0123.'"}],
        },
    ),
    (
        "Conversation (Haiku)",
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Tell me a one-liner joke about programming."}],
        },
    ),
]


# OpenAI-format requests for non-Anthropic models (via /v1/chat/completions)
OPENAI_REQUESTS: list[tuple[str, dict]] = [
    (
        "Code generation (GPT-5.2)",
        {
            "model": "gpt-5.2-chat-latest",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": "Write a TypeScript function that debounces an async callback, cancelling pending invocations."}],
        },
    ),
    (
        "Summarization (Qwen3 235B)",
        {
            "model": "qwen.qwen3-vl-235b-a22b",
            "max_tokens": 200,
            "messages": [{"role": "user", "content": "Summarize in 3 bullet points: Retrieval-augmented generation combines a retrieval component with a generative model. Documents are indexed into a vector store, and at query time the most relevant chunks are fetched and prepended to the prompt, grounding the model's output in factual sources."}],
        },
    ),
    (
        "Classification (Gemma 27B)",
        {
            "model": "google.gemma-3-27b-it",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "Classify this support ticket as billing, technical, or general: 'I was charged twice for my subscription this month and need a refund.'"}],
        },
    ),
    (
        "Reasoning (Kimi K2)",
        {
            "model": "moonshot.kimi-k2-thinking",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "A bat and a ball together cost $1.10. The bat costs $1.00 more than the ball. How much does the ball cost? Show your work."}],
        },
    ),
]


class _ServiceDown(Exception):
    """Raised when a request fails due to connectivity, not an HTTP error."""


def _send(
    client: httpx.Client,
    desc: str,
    body: dict,
    *,
    endpoint: str,
    headers: dict,
    token_fields: tuple[str, str] = ("input_tokens", "output_tokens"),
) -> bool:
    """Send a request, print summary. Returns True on success, False on HTTP error.

    Raises _ServiceDown on connectivity failure so callers can skip remaining requests.
    """
    print(f"\n  {desc}...")
    start = time.monotonic()

    try:
        resp = client.post(
            f"{PROXY_URL}{endpoint}", json=body, headers=headers, timeout=60.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        print(f"    SKIPPED: {type(exc).__name__}")
        raise _ServiceDown(str(exc)) from exc

    elapsed = (time.monotonic() - start) * 1000

    if resp.status_code >= 400:
        print(f"    ERROR {resp.status_code}: {resp.text[:200]}")
        return False

    data = resp.json()
    usage = data.get("usage", {})

    print(f"    Model: {body.get('model', 'unknown')}")
    print(f"    Latency: {elapsed:.0f}ms")
    print(f"    Tokens: {usage.get(token_fields[0], 0)} in / {usage.get(token_fields[1], 0)} out")
    print(f"    Status: {resp.status_code}")
    return True


def send_request(client: httpx.Client, desc: str, body: dict) -> bool:
    """Send an Anthropic-format request through /v1/messages."""
    return _send(client, desc, body, endpoint="/v1/messages", headers=HEADERS)


def send_openai_request(client: httpx.Client, desc: str, body: dict) -> bool:
    """Send an OpenAI-format request through /v1/chat/completions."""
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "content-type": "application/json",
    }
    return _send(
        client, desc, body,
        endpoint="/v1/chat/completions",
        headers=headers,
        token_fields=("prompt_tokens", "completion_tokens"),
    )


def main() -> None:
    _check_api_key()

    total = len(REQUESTS) + len(OPENAI_REQUESTS)
    print(f"Sending {total} requests through AgentProof proxy at {PROXY_URL}")
    print("Each request has a ~2s pause for narration.\n")

    with httpx.Client() as client:
        # Quick health check
        try:
            health = client.get(f"{PROXY_URL}/health", timeout=5.0)
            if health.status_code != 200:
                print(f"WARNING: Health check returned {health.status_code}")
        except (httpx.ConnectError, httpx.TimeoutException):
            print(f"ERROR: Cannot connect to {PROXY_URL}. Is the stack running? (make dev)")
            sys.exit(1)

        anthropic_ok = 0
        for i, (desc, body) in enumerate(REQUESTS):
            try:
                if send_request(client, desc, body):
                    anthropic_ok += 1
            except _ServiceDown:
                print("\n  Proxy unreachable — skipping remaining Anthropic requests.")
                break
            if i < len(REQUESTS) - 1:
                time.sleep(2)

        openai_ok = 0
        if OPENAI_REQUESTS:
            print(f"\nSending {len(OPENAI_REQUESTS)} OpenAI-format requests (requires LiteLLM)...\n")
            for i, (desc, body) in enumerate(OPENAI_REQUESTS):
                try:
                    if send_openai_request(client, desc, body):
                        openai_ok += 1
                except _ServiceDown:
                    print("\n  LiteLLM not available — skipping remaining OpenAI requests.")
                    print("  (Run 'make dev-proxy' to enable multi-model traffic.)")
                    break
                if i < len(OPENAI_REQUESTS) - 1:
                    time.sleep(2)

    print(f"\n  Done! {anthropic_ok}/{len(REQUESTS)} Anthropic + {openai_ok}/{len(OPENAI_REQUESTS)} OpenAI requests sent.")
    print("  Check the dashboard at http://localhost:8081 to see live data.")


if __name__ == "__main__":
    main()
