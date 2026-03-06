"""Unit tests for the transparent HTTP proxy routes."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from blockthrough.api.routes.proxy import (
    _StreamAccumulator,
    _build_upstream_headers,
    _compute_cost,
    _detect_framework_from_headers,
    _extract_trace_from_headers,
    _infer_provider,
    _is_plan_mode,
    _is_plan_mode_anthropic,
    _maybe_route,
    _request_uses_tools,
    _request_uses_tools_anthropic,
)
from blockthrough.routing.types import RoutingDecision
from blockthrough.types import LLMEvent, TaskType


# ---------------------------------------------------------------------------
# _compute_cost
# ---------------------------------------------------------------------------

class TestComputeCost:
    def test_known_model(self):
        # claude-haiku-4-5-20251001: input=0.0008/1k, output=0.004/1k
        cost = _compute_cost("claude-haiku-4-5-20251001", 1000, 500)
        expected = 0.0008 * 1 + 0.004 * 0.5  # 0.0008 + 0.002 = 0.0028
        assert abs(cost - expected) < 1e-9

    def test_unknown_model(self):
        assert _compute_cost("mystery-model-9000", 100, 50) == 0.0

    def test_zero_tokens(self):
        assert _compute_cost("claude-haiku-4-5-20251001", 0, 0) == 0.0

    def test_gpt4o_mini(self):
        # gpt-4o-mini: input=0.00015/1k, output=0.0006/1k
        cost = _compute_cost("gpt-4o-mini", 2000, 1000)
        expected = 0.00015 * 2 + 0.0006 * 1
        assert abs(cost - expected) < 1e-9


# ---------------------------------------------------------------------------
# _extract_trace_from_headers
# ---------------------------------------------------------------------------

class TestExtractTraceFromHeaders:
    def test_x_trace_id(self):
        assert _extract_trace_from_headers({"x-trace-id": "abc123"}) == "abc123"

    def test_x_request_id_fallback(self):
        assert _extract_trace_from_headers({"x-request-id": "req-456"}) == "req-456"

    def test_x_trace_id_takes_priority(self):
        headers = {"x-trace-id": "trace", "x-request-id": "req"}
        assert _extract_trace_from_headers(headers) == "trace"

    def test_generates_uuid_when_missing(self):
        result = _extract_trace_from_headers({})
        # Should be a valid hex string (UUID without dashes)
        assert len(result) == 32
        uuid.UUID(result, version=4)  # shouldn't raise


# ---------------------------------------------------------------------------
# _detect_framework_from_headers
# ---------------------------------------------------------------------------

class TestDetectFrameworkFromHeaders:
    def test_claude_code(self):
        assert _detect_framework_from_headers({"user-agent": "claude-code/1.0"}) == "claude-code"

    def test_langchain(self):
        assert _detect_framework_from_headers({"user-agent": "python langchain/0.1"}) == "langchain"

    def test_unknown(self):
        assert _detect_framework_from_headers({"user-agent": "curl/8.0"}) is None

    def test_empty(self):
        assert _detect_framework_from_headers({}) is None


# ---------------------------------------------------------------------------
# _infer_provider
# ---------------------------------------------------------------------------

class TestInferProvider:
    def test_claude(self):
        assert _infer_provider("claude-haiku-4-5-20251001") == "anthropic"
        assert _infer_provider("claude-opus-4-20250514") == "anthropic"

    def test_openai(self):
        assert _infer_provider("gpt-4o") == "openai"
        assert _infer_provider("o1-preview") == "openai"
        assert _infer_provider("o3-mini") == "openai"

    def test_unknown(self):
        assert _infer_provider("llama-3-70b") == "unknown"


# ---------------------------------------------------------------------------
# _build_upstream_headers
# ---------------------------------------------------------------------------

class TestBuildUpstreamHeaders:
    def test_strips_hop_by_hop(self):
        headers = {
            "Authorization": "Bearer sk-123",
            "Content-Type": "application/json",
            "Host": "localhost:8100",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
        }
        result = _build_upstream_headers(headers)
        assert "Authorization" in result
        assert "Content-Type" in result
        assert "Host" not in result
        assert "Connection" not in result
        assert "Transfer-Encoding" not in result


# ---------------------------------------------------------------------------
# _StreamAccumulator
# ---------------------------------------------------------------------------

class TestStreamAccumulator:
    def test_content_deltas(self):
        acc = _StreamAccumulator()
        acc.feed_chunk({
            "id": "chatcmpl-1",
            "model": "claude-haiku-4-5-20251001",
            "choices": [{"delta": {"content": "Hello"}, "index": 0}],
        }, elapsed_ms=50.0)
        acc.feed_chunk({
            "choices": [{"delta": {"content": " world"}, "index": 0}],
        }, elapsed_ms=80.0)

        assert acc.full_content == "Hello world"
        assert acc.model == "claude-haiku-4-5-20251001"
        assert acc.response_id == "chatcmpl-1"
        assert acc.ttft_ms == 50.0

    def test_tool_call_reconstruction(self):
        acc = _StreamAccumulator()
        # First chunk: tool call start
        acc.feed_chunk({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"name": "get_weather", "arguments": '{"ci'},
                    }],
                },
                "index": 0,
            }],
        }, elapsed_ms=10.0)
        # Second chunk: continue arguments
        acc.feed_chunk({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": 'ty":"NYC"}'},
                    }],
                },
                "index": 0,
            }],
        }, elapsed_ms=20.0)

        records = acc.tool_call_records
        assert len(records) == 1
        assert records[0].tool_name == "get_weather"
        # Arguments should be the concatenated JSON
        assert records[0].args_hash  # non-empty hash

    def test_multiple_tool_calls(self):
        acc = _StreamAccumulator()
        acc.feed_chunk({
            "choices": [{
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"name": "tool_a", "arguments": "{}"}},
                        {"index": 1, "function": {"name": "tool_b", "arguments": "{}"}},
                    ],
                },
                "index": 0,
            }],
        }, elapsed_ms=10.0)

        records = acc.tool_call_records
        assert len(records) == 2
        assert records[0].tool_name == "tool_a"
        assert records[1].tool_name == "tool_b"

    def test_usage_from_final_chunk(self):
        acc = _StreamAccumulator()
        acc.feed_chunk({
            "choices": [{"delta": {"content": "hi"}, "index": 0}],
        }, elapsed_ms=10.0)
        # Final chunk with usage
        acc.feed_chunk({
            "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25},
        }, elapsed_ms=500.0)

        assert acc.prompt_tokens == 100
        assert acc.completion_tokens == 25

    def test_finish_reason(self):
        acc = _StreamAccumulator()
        acc.feed_chunk({
            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
        }, elapsed_ms=100.0)
        assert acc.finish_reason == "stop"

    def test_empty_deltas(self):
        """Graceful handling of chunks with empty or missing deltas."""
        acc = _StreamAccumulator()
        acc.feed_chunk({"choices": [{}]}, elapsed_ms=10.0)
        acc.feed_chunk({"choices": [{"delta": {}}]}, elapsed_ms=20.0)
        acc.feed_chunk({"choices": []}, elapsed_ms=30.0)
        assert acc.full_content == ""
        assert acc.ttft_ms is None

    def test_empty_stream(self):
        acc = _StreamAccumulator()
        assert acc.full_content == ""
        assert acc.tool_call_records == []
        assert acc.ttft_ms is None
        assert acc.prompt_tokens == 0
        assert acc.completion_tokens == 0


# ---------------------------------------------------------------------------
# Integration tests — TestClient with mocked httpx
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_app():
    """Create a test app with mocked http_client and event_queue."""
    from fastapi.testclient import TestClient

    from blockthrough.api.routes.proxy import router

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    queue: asyncio.Queue[LLMEvent] = asyncio.Queue(maxsize=100)
    app.state.event_queue = queue
    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)

    client = TestClient(app)
    return client, app, queue


class TestNonStreamingProxy:
    def test_success_response_forwarded(self, mock_app):
        client, app, queue = mock_app

        upstream_body = {
            "id": "chatcmpl-abc",
            "model": "claude-haiku-4-5-20251001",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = upstream_body
        app.state.http_client.post = AsyncMock(return_value=mock_resp)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "claude-haiku-4-5-20251001", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-test"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "Hello!"

        # Event should be enqueued
        assert not queue.empty()
        event: LLMEvent = queue.get_nowait()
        assert event.model == "claude-haiku-4-5-20251001"
        assert event.prompt_tokens == 10
        assert event.completion_tokens == 5
        assert event.status.value == "success"
        assert event.provider == "anthropic"

    def test_error_response_captured(self, mock_app):
        client, app, queue = mock_app

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.json.return_value = {
            "error": {"type": "rate_limit_error", "message": "Too many requests"},
        }
        app.state.http_client.post = AsyncMock(return_value=mock_resp)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert resp.status_code == 429
        event: LLMEvent = queue.get_nowait()
        assert event.status.value == "failure"
        assert event.error_type == "rate_limit_error"

    def test_tool_calls_captured(self, mock_app):
        client, app, queue = mock_app

        upstream_body = {
            "id": "chatcmpl-tc",
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"NYC"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = upstream_body
        app.state.http_client.post = AsyncMock(return_value=mock_resp)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "weather?"}]},
        )

        assert resp.status_code == 200
        event: LLMEvent = queue.get_nowait()
        assert event.has_tool_calls is True
        assert len(event.tool_calls) == 1
        assert event.tool_calls[0].tool_name == "get_weather"


class TestStreamingProxy:
    def test_chunks_forwarded_and_event_built(self, mock_app):
        client, app, queue = mock_app

        sse_lines = [
            'data: {"id":"chatcmpl-s1","model":"claude-haiku-4-5-20251001","choices":[{"delta":{"role":"assistant","content":""},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"Hi"},"index":0}]}',
            'data: {"choices":[{"delta":{"content":" there"},"index":0}]}',
            'data: {"choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":3}}',
            "data: [DONE]",
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        mock_upstream_resp = AsyncMock()
        mock_upstream_resp.status_code = 200
        mock_upstream_resp.aiter_lines = mock_aiter_lines
        mock_upstream_resp.aclose = AsyncMock()

        app.state.http_client.build_request = MagicMock(return_value=MagicMock())
        app.state.http_client.send = AsyncMock(return_value=mock_upstream_resp)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku-4-5-20251001",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )

        assert resp.status_code == 200
        # All SSE lines should be present in response
        body = resp.text
        assert "Hi" in body
        assert "there" in body
        assert "[DONE]" in body

        # Event should be enqueued after stream completes
        event: LLMEvent = queue.get_nowait()
        assert event.prompt_tokens == 8
        assert event.completion_tokens == 3
        assert event.model == "claude-haiku-4-5-20251001"
        assert event.status.value == "success"
        assert event.time_to_first_token_ms is not None

    def test_stream_options_injected(self, mock_app):
        """Verify that stream_options.include_usage=true is injected."""
        client, app, queue = mock_app

        captured_request = None

        def capture_build_request(method, url, **kwargs):
            nonlocal captured_request
            captured_request = kwargs.get("json", {})
            return MagicMock()

        async def mock_aiter_lines():
            yield 'data: {"choices":[{"delta":{"content":"ok"},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
            yield "data: [DONE]"

        mock_upstream_resp = AsyncMock()
        mock_upstream_resp.status_code = 200
        mock_upstream_resp.aiter_lines = mock_aiter_lines
        mock_upstream_resp.aclose = AsyncMock()

        app.state.http_client.build_request = MagicMock(side_effect=capture_build_request)
        app.state.http_client.send = AsyncMock(return_value=mock_upstream_resp)

        client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
            },
        )

        assert captured_request is not None
        assert captured_request.get("stream_options", {}).get("include_usage") is True


class TestModelsPassthrough:
    def test_models_forwarded(self, mock_app):
        client, app, _ = mock_app

        models_data = {"data": [{"id": "gpt-4o"}, {"id": "claude-haiku-4-5-20251001"}]}
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = models_data
        app.state.http_client.get = AsyncMock(return_value=mock_resp)

        resp = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2


# ---------------------------------------------------------------------------
# _request_uses_tools (OpenAI format)
# ---------------------------------------------------------------------------

class TestRequestUsesTools:
    def test_tools_array_present(self):
        body = {"tools": [{"type": "function", "function": {"name": "get_weather"}}], "messages": []}
        assert _request_uses_tools(body) is True

    def test_functions_array_present(self):
        body = {"functions": [{"name": "get_weather"}], "messages": []}
        assert _request_uses_tools(body) is True

    def test_assistant_tool_calls_in_history(self):
        body = {"messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "x"}}]},
            {"role": "tool", "content": "result"},
        ]}
        assert _request_uses_tools(body) is True

    def test_tool_role_in_history(self):
        body = {"messages": [{"role": "tool", "content": "result"}]}
        assert _request_uses_tools(body) is True

    def test_no_tools(self):
        body = {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]}
        assert _request_uses_tools(body) is False

    def test_empty_body(self):
        assert _request_uses_tools({}) is False


# ---------------------------------------------------------------------------
# _request_uses_tools_anthropic (Anthropic format)
# ---------------------------------------------------------------------------

class TestRequestUsesToolsAnthropic:
    def test_tools_array_present(self):
        body = {"tools": [{"name": "get_weather"}], "messages": []}
        assert _request_uses_tools_anthropic(body) is True

    def test_tool_use_block_in_history(self):
        body = {"messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "get_weather", "input": {}}]},
        ]}
        assert _request_uses_tools_anthropic(body) is True

    def test_tool_result_block_in_history(self):
        body = {"messages": [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        ]}
        assert _request_uses_tools_anthropic(body) is True

    def test_no_tools(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        assert _request_uses_tools_anthropic(body) is False

    def test_string_content_no_tools(self):
        body = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]}
        assert _request_uses_tools_anthropic(body) is False

    def test_empty_body(self):
        assert _request_uses_tools_anthropic({}) is False


# ---------------------------------------------------------------------------
# _is_plan_mode (OpenAI format — system in messages)
# ---------------------------------------------------------------------------

class TestIsPlanMode:
    def test_plan_mode_active_string(self):
        body = {"messages": [
            {"role": "system", "content": "You are Claude Code. Plan mode is active. Do not write code."},
            {"role": "user", "content": "Design a REST API"},
        ]}
        assert _is_plan_mode(body) is True

    def test_plan_mode_still_active(self):
        body = {"messages": [
            {"role": "system", "content": "Plan mode still active — read-only."},
        ]}
        assert _is_plan_mode(body) is True

    def test_no_plan_mode(self):
        body = {"messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Write a function"},
        ]}
        assert _is_plan_mode(body) is False

    def test_content_block_array_format(self):
        body = {"messages": [
            {"role": "system", "content": [
                {"type": "text", "text": "You are Claude Code."},
                {"type": "text", "text": "Plan mode is active. Read-only."},
            ]},
        ]}
        assert _is_plan_mode(body) is True

    def test_content_block_array_no_plan_mode(self):
        body = {"messages": [
            {"role": "system", "content": [
                {"type": "text", "text": "You are a helpful assistant."},
            ]},
        ]}
        assert _is_plan_mode(body) is False

    def test_no_system_message(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert _is_plan_mode(body) is False

    def test_empty_messages(self):
        assert _is_plan_mode({"messages": []}) is False
        assert _is_plan_mode({}) is False


# ---------------------------------------------------------------------------
# _is_plan_mode_anthropic (Anthropic format — system as top-level field)
# ---------------------------------------------------------------------------

class TestIsPlanModeAnthropic:
    def test_plan_mode_in_system_string(self):
        body = {
            "system": "You are Claude Code. Plan mode is active.",
            "messages": [{"role": "user", "content": "Design an API"}],
        }
        assert _is_plan_mode_anthropic(body) is True

    def test_plan_mode_in_system_blocks(self):
        body = {
            "system": [
                {"type": "text", "text": "You are Claude Code."},
                {"type": "text", "text": "Plan mode is active. Read-only."},
            ],
            "messages": [{"role": "user", "content": "Plan this"}],
        }
        assert _is_plan_mode_anthropic(body) is True

    def test_no_plan_mode_anthropic(self):
        body = {
            "system": "You are a helpful coding assistant.",
            "messages": [{"role": "user", "content": "Write code"}],
        }
        assert _is_plan_mode_anthropic(body) is False

    def test_no_system_field(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert _is_plan_mode_anthropic(body) is False


# ---------------------------------------------------------------------------
# Confidence-gated routing
# ---------------------------------------------------------------------------

def _make_routing_request(confidence_threshold=0.4, routing_enabled=True):
    """Build a mock Request with routing state configured."""
    request = MagicMock()
    request.app.state.routing_enabled = routing_enabled
    request.app.state.routing_confidence_threshold = confidence_threshold
    request.app.state.fitness_cache = MagicMock()
    request.app.state.routing_policy = MagicMock(version=1)
    request.app.state.decision_queue = None
    request.headers = {}
    return request


class TestConfidenceGate:
    """Confidence gate in _maybe_route: low confidence → passthrough."""

    @pytest.mark.asyncio
    async def test_high_confidence_routes(self):
        """Confidence above threshold → routing proceeds normally."""
        request = _make_routing_request(confidence_threshold=0.4)

        async def classify_fn(request, body, pt, ct):
            return TaskType.CODE_GENERATION, 0.85, "hash123"

        with pytest.MonkeyPatch.context() as mp:
            # Mock resolve() to return a routed decision
            routed = RoutingDecision(
                selected_model="claude-haiku-4-5-20251001",
                reason="cheapest_above_quality",
                was_overridden=True,
                policy_rule_id=0,
            )
            mp.setattr("blockthrough.api.routes.proxy.resolve", lambda **kw: routed)
            mp.setattr("blockthrough.api.routes.proxy.record_decision", lambda d: None)

            model, decision, (tt, conf, sh) = await _maybe_route(
                request,
                {"messages": [{"role": "user", "content": "write a function"}]},
                "claude-sonnet-4-20250514",
                classify_fn,
            )

        assert model == "claude-haiku-4-5-20251001"
        assert decision.was_overridden is True
        assert decision.confidence == 0.85

    @pytest.mark.asyncio
    async def test_low_confidence_passthrough(self):
        """Confidence below threshold → passthrough, keep requested model."""
        request = _make_routing_request(confidence_threshold=0.4)

        async def classify_fn(request, body, pt, ct):
            return TaskType.CONVERSATION, 0.2, "hash456"

        model, decision, (tt, conf, sh) = await _maybe_route(
            request,
            {"messages": [{"role": "user", "content": "hello"}]},
            "claude-sonnet-4-20250514",
            classify_fn,
        )

        assert model == "claude-sonnet-4-20250514"
        assert decision is not None
        assert decision.was_overridden is False
        assert "classifier confidence" in decision.reason
        assert "0.20" in decision.reason

    @pytest.mark.asyncio
    async def test_none_confidence_passthrough(self):
        """None confidence → passthrough."""
        request = _make_routing_request(confidence_threshold=0.4)

        async def classify_fn(request, body, pt, ct):
            return TaskType.CONVERSATION, None, "hash789"

        model, decision, (tt, conf, sh) = await _maybe_route(
            request,
            {"messages": [{"role": "user", "content": "hey"}]},
            "claude-sonnet-4-20250514",
            classify_fn,
        )

        assert model == "claude-sonnet-4-20250514"
        assert decision is not None
        assert decision.was_overridden is False
        assert "classifier confidence" in decision.reason

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_routes(self):
        """Confidence exactly at threshold (0.4) → routing proceeds (gate is <, not <=)."""
        request = _make_routing_request(confidence_threshold=0.4)

        async def classify_fn(request, body, pt, ct):
            return TaskType.CODE_GENERATION, 0.4, "hash_exact"

        with pytest.MonkeyPatch.context() as mp:
            routed = RoutingDecision(
                selected_model="claude-haiku-4-5-20251001",
                reason="cheapest_above_quality",
                was_overridden=True,
                policy_rule_id=0,
            )
            mp.setattr("blockthrough.api.routes.proxy.resolve", lambda **kw: routed)
            mp.setattr("blockthrough.api.routes.proxy.record_decision", lambda d: None)

            model, decision, (tt, conf, sh) = await _maybe_route(
                request,
                {"messages": [{"role": "user", "content": "write code"}]},
                "claude-sonnet-4-20250514",
                classify_fn,
            )

        assert model == "claude-haiku-4-5-20251001"
        assert decision.was_overridden is True

    @pytest.mark.asyncio
    async def test_plan_mode_ignores_confidence(self):
        """Plan mode → force Opus regardless of confidence."""
        request = _make_routing_request(confidence_threshold=0.4)

        async def classify_fn(request, body, pt, ct):
            return TaskType.CONVERSATION, 0.3, "hash_plan"

        body = {"messages": [
            {"role": "system", "content": "Plan mode is active. Do not write code."},
            {"role": "user", "content": "Design an API"},
        ]}

        model, decision, _ = await _maybe_route(
            request, body, "claude-sonnet-4-20250514", classify_fn,
        )

        assert model == "claude-opus-4-6"
        assert decision is None

    @pytest.mark.asyncio
    async def test_threshold_fallback_uses_config_not_magic_number(self):
        """H2: Missing routing_confidence_threshold on app.state uses config default (0.4), not 0.7."""
        request = MagicMock()
        request.app.state.routing_enabled = True
        request.app.state.fitness_cache = MagicMock()
        request.app.state.routing_policy = MagicMock(version=1)
        request.app.state.decision_queue = None
        request.headers = {}
        # Intentionally do NOT set routing_confidence_threshold
        del request.app.state.routing_confidence_threshold

        call_count = 0

        async def classify_fn(req, body, pt, ct):
            nonlocal call_count
            call_count += 1
            # Confidence 0.5: above 0.4 (config default) but below 0.7 (old magic number)
            return TaskType.CODE_GENERATION, 0.5, "hash"

        with pytest.MonkeyPatch.context() as mp:
            routed = RoutingDecision(
                selected_model="claude-haiku-4-5-20251001",
                reason="cheapest",
                was_overridden=True,
                policy_rule_id=0,
            )
            mp.setattr("blockthrough.api.routes.proxy.resolve", lambda **kw: routed)
            mp.setattr("blockthrough.api.routes.proxy.record_decision", lambda d: None)

            model, decision, _ = await _maybe_route(
                request,
                {"messages": [{"role": "user", "content": "test"}]},
                "claude-sonnet-4-20250514",
                classify_fn,
            )

        # Should route (0.5 >= 0.4), not passthrough (as it would with 0.7 threshold)
        assert model == "claude-haiku-4-5-20251001"
        assert decision.was_overridden is True

    @pytest.mark.asyncio
    async def test_pre_classification_reused_not_reclassified(self):
        """H1: Classification from routing is reused in event logging."""
        request = _make_routing_request(confidence_threshold=0.4)
        classify_call_count = 0

        async def classify_fn(req, body, pt, ct):
            nonlocal classify_call_count
            classify_call_count += 1
            return TaskType.CODE_GENERATION, 0.85, "hash_first"

        with pytest.MonkeyPatch.context() as mp:
            routed = RoutingDecision(
                selected_model="claude-haiku-4-5-20251001",
                reason="cheapest",
                was_overridden=True,
                policy_rule_id=0,
            )
            mp.setattr("blockthrough.api.routes.proxy.resolve", lambda **kw: routed)
            mp.setattr("blockthrough.api.routes.proxy.record_decision", lambda d: None)

            model, decision, pre_class = await _maybe_route(
                request,
                {"messages": [{"role": "user", "content": "write code"}]},
                "claude-sonnet-4-20250514",
                classify_fn,
            )

        assert classify_call_count == 1
        assert pre_class == (TaskType.CODE_GENERATION, 0.85, "hash_first")


# ---------------------------------------------------------------------------
# List content normalization (H4)
# ---------------------------------------------------------------------------

class TestListContentNormalization:
    def test_multi_block_user_content_no_crash(self, mock_app):
        """H4: Multi-block content lists should not crash extract_keywords."""
        client, app, queue = mock_app

        upstream_body = {
            "id": "chatcmpl-multi",
            "model": "gpt-4o",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = upstream_body
        app.state.http_client.post = AsyncMock(return_value=mock_resp)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": [
                        {"type": "text", "text": "Describe this image"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ]},
                ],
            },
        )

        assert resp.status_code == 200
        event: LLMEvent = queue.get_nowait()
        assert event.status.value == "success"


# ---------------------------------------------------------------------------
# Queue drop counters (M3)
# ---------------------------------------------------------------------------

class TestQueueDropCounters:
    def test_get_queue_drop_counts_returns_dict(self):
        from blockthrough.api.routes.proxy import get_queue_drop_counts
        counts = get_queue_drop_counts()
        assert "event_queue_drops" in counts
        assert "decision_queue_drops" in counts
        assert "benchmark_queue_drops" in counts
        assert all(isinstance(v, int) for v in counts.values())
