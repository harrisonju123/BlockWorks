"""Integration tests: Proxy-to-routing E2E.

Verifies the full proxy routing flow:
  request -> _maybe_route -> resolve() -> model override -> response
  + routing decision is recorded in the recent decisions deque
  + when routing is disabled, the original model passes through unchanged

Mocks the upstream httpx client to avoid real API calls.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentproof.api.app import app
from agentproof.api.routes.routing import _recent_decisions
from agentproof.benchmarking.types import FitnessEntry
from agentproof.routing.router import FitnessCache
from agentproof.routing.types import RoutingPolicy, RoutingRule, SelectionCriteria
from agentproof.types import LLMEvent

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_openai_response(model: str = "claude-sonnet-4-20250514") -> dict:
    """Build a minimal OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from the mock!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }


def _mock_httpx_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Create a fake httpx.Response without hitting the network."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("POST", "http://upstream/v1/chat/completions"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def event_queue() -> asyncio.Queue[LLMEvent]:
    return asyncio.Queue(maxsize=100)


@pytest.fixture
def mock_http_client():
    """AsyncMock that returns a canned OpenAI response for POST requests."""
    client = AsyncMock(spec=httpx.AsyncClient)
    # post() returns a response with the model from the request body
    async def _post(url, *, json=None, headers=None, **kwargs):
        model = (json or {}).get("model", "claude-sonnet-4-20250514")
        return _mock_httpx_response(_build_openai_response(model))

    client.post = AsyncMock(side_effect=_post)
    return client


@pytest.fixture
def fitness_cache_with_entries() -> FitnessCache:
    """FitnessCache pre-loaded with entries that make haiku cheaper for code_generation."""
    cache = FitnessCache(ttl_s=600)
    cache.update([
        FitnessEntry(
            task_type="code_generation",
            model="claude-haiku-4-5-20251001",
            avg_quality=0.85,
            avg_cost=0.0003,
            avg_latency=200.0,
            sample_size=100,
        ),
        FitnessEntry(
            task_type="code_generation",
            model="claude-sonnet-4-20250514",
            avg_quality=0.95,
            avg_cost=0.003,
            avg_latency=800.0,
            sample_size=100,
        ),
        FitnessEntry(
            task_type="summarization",
            model="gpt-4o-mini",
            avg_quality=0.80,
            avg_cost=0.0001,
            avg_latency=150.0,
            sample_size=50,
        ),
    ])
    return cache


@pytest.fixture
def routing_policy() -> RoutingPolicy:
    """Policy that routes code_generation to cheapest model above quality floor."""
    return RoutingPolicy(
        version=1,
        rules=[
            RoutingRule(
                task_type="code_generation",
                criteria=SelectionCriteria.CHEAPEST_ABOVE_QUALITY,
                min_quality=0.8,
                fallback="claude-sonnet-4-20250514",
            ),
        ],
    )


@pytest.fixture
async def routed_client(
    event_queue: asyncio.Queue,
    mock_http_client: AsyncMock,
    fitness_cache_with_entries: FitnessCache,
    routing_policy: RoutingPolicy,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Test client with routing enabled, fitness cache populated, and upstream mocked."""
    # Clear the module-level decisions deque before each test
    _recent_decisions.clear()

    # Wire up app.state without running the real lifespan (which connects to upstream)
    app.state.http_client = mock_http_client
    app.state.anthropic_client = mock_http_client
    app.state.event_queue = event_queue
    app.state.routing_enabled = True
    app.state.fitness_cache = fitness_cache_with_entries
    app.state.routing_policy = routing_policy

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    # Cleanup: restore routing_enabled to False so other tests aren't affected
    app.state.routing_enabled = False
    _recent_decisions.clear()


@pytest.fixture
async def unrouted_client(
    event_queue: asyncio.Queue,
    mock_http_client: AsyncMock,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Test client with routing disabled -- original model should pass through."""
    _recent_decisions.clear()

    app.state.http_client = mock_http_client
    app.state.anthropic_client = mock_http_client
    app.state.event_queue = event_queue
    app.state.routing_enabled = False

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    _recent_decisions.clear()


# ---------------------------------------------------------------------------
# Tests: routing enabled
# ---------------------------------------------------------------------------

class TestProxyRoutingEnabled:
    """When routing is enabled and the fitness cache has data, requests should
    be rerouted based on the policy and the decision should be recorded."""

    async def test_model_overridden_to_cheaper(self, routed_client: httpx.AsyncClient):
        """A code_generation request for sonnet should be rerouted to haiku (cheaper)."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "system", "content": "You are a code assistant. Write code."},
                {"role": "user", "content": "Implement a binary search function."},
            ],
        }

        resp = await routed_client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200

        # The mock upstream received the rerouted model in the request body
        call_args = app.state.http_client.post.call_args
        sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert sent_body["model"] == "claude-haiku-4-5-20251001", (
            "Expected the proxy to rewrite model to the cheaper alternative"
        )

    async def test_routing_decision_recorded(self, routed_client: httpx.AsyncClient):
        """After a routed request, the decision should appear in the recent decisions deque."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "system", "content": "You are a code assistant. Write code."},
                {"role": "user", "content": "Write a quicksort function."},
            ],
        }

        await routed_client.post("/v1/chat/completions", json=body)

        assert len(_recent_decisions) >= 1, "Expected at least one routing decision recorded"
        decision = _recent_decisions[-1]
        assert decision.selected_model == "claude-haiku-4-5-20251001"
        assert decision.was_overridden is True
        assert decision.policy_rule_id is not None

    async def test_event_enqueued_with_routed_model(
        self, routed_client: httpx.AsyncClient, event_queue: asyncio.Queue
    ):
        """The LLMEvent enqueued by the proxy should reflect the routed model."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "system", "content": "You are a code assistant. Write code."},
                {"role": "user", "content": "Implement a merge sort."},
            ],
        }

        await routed_client.post("/v1/chat/completions", json=body)

        # The event queue should have the event with the rerouted model
        assert not event_queue.empty(), "Expected an event in the queue"
        event: LLMEvent = event_queue.get_nowait()
        assert event.model == "claude-haiku-4-5-20251001", (
            "Event model should reflect the routed model, not the original"
        )

    async def test_no_matching_rule_passes_through(self, routed_client: httpx.AsyncClient):
        """A task type with no matching rule should pass the original model unchanged."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                # Summarization prompt -- no rule for this task type in our policy
                {"role": "system", "content": "You summarize text concisely."},
                {"role": "user", "content": "Summarize this article about climate change."},
            ],
        }

        resp = await routed_client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200

        # The upstream should have received the original model (no override)
        call_args = app.state.http_client.post.call_args
        sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert sent_body["model"] == "claude-sonnet-4-20250514"

    async def test_response_body_returned_to_caller(self, routed_client: httpx.AsyncClient):
        """The proxy should return the upstream response body to the caller as-is."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "system", "content": "You are a code assistant. Write code."},
                {"role": "user", "content": "Write hello world."},
            ],
        }

        resp = await routed_client.post("/v1/chat/completions", json=body)
        data = resp.json()

        assert "choices" in data
        assert data["choices"][0]["message"]["content"] == "Hello from the mock!"


# ---------------------------------------------------------------------------
# Tests: routing disabled
# ---------------------------------------------------------------------------

class TestProxyRoutingDisabled:
    """When routing is disabled, the original model should always pass through."""

    async def test_original_model_preserved(self, unrouted_client: httpx.AsyncClient):
        """With routing off, even a code_generation prompt should keep the original model."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "system", "content": "You are a code assistant. Write code."},
                {"role": "user", "content": "Implement a binary search function."},
            ],
        }

        resp = await unrouted_client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200

        call_args = app.state.http_client.post.call_args
        sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert sent_body["model"] == "claude-sonnet-4-20250514"

    async def test_no_decisions_recorded(self, unrouted_client: httpx.AsyncClient):
        """No routing decisions should be recorded when routing is off."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "system", "content": "You are a code assistant. Write code."},
                {"role": "user", "content": "Write a function."},
            ],
        }

        await unrouted_client.post("/v1/chat/completions", json=body)
        assert len(_recent_decisions) == 0

    async def test_event_enqueued_with_original_model(
        self, unrouted_client: httpx.AsyncClient, event_queue: asyncio.Queue
    ):
        """The enqueued event should have the originally requested model."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hello."},
            ],
        }

        await unrouted_client.post("/v1/chat/completions", json=body)

        assert not event_queue.empty()
        event: LLMEvent = event_queue.get_nowait()
        # The mock returns whatever model was sent, so verify consistency
        assert event.model == "claude-sonnet-4-20250514"
