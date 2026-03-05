"""Transparent HTTP proxy that sits between Claude Code and an upstream LLM provider.

Captures every request/response as an LLMEvent and enqueues it for the
EventWriter pipeline — same data model as the LiteLLM callback, but
works without installing anything on the proxy host.

Routes:
  POST /v1/chat/completions  — proxy + capture (streaming & non-streaming)
  GET  /v1/models            — passthrough, no capture
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from blockthrough.benchmarking.mirror import should_sample
from blockthrough.benchmarking.types import BenchmarkConfig
from blockthrough.classifier.llm_classifier import llm_classify
from blockthrough.classifier.rules import (
    classify,
    compute_token_ratio,
    extract_keywords,
)
from blockthrough.config import get_config
from blockthrough.classifier.taxonomy import ClassifierInput
from blockthrough.models import MODEL_CATALOG, get_anthropic_models
from blockthrough.pipeline.context import FRAMEWORK_HINTS
from blockthrough.pipeline.hasher import hash_content
from blockthrough.api.routes.routing import record_decision
from blockthrough.routing.router import FitnessCache, resolve
from blockthrough.routing.convert import anthropic_to_openai_request, openai_to_anthropic_response
from blockthrough.routing.sanitize import repair_tool_pairing, sanitize_for_target, strip_unsupported_params
from blockthrough.routing.types import RoutingDecision
from blockthrough.routing.writer import DecisionRecord
from blockthrough.types import EventStatus, LLMEvent, TaskType, ToolCallRecord
from blockthrough.utils import infer_provider, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

# Headers that must not be forwarded between hops (RFC 2616 §13.5.1)
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length",  # httpx recalculates
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    info = MODEL_CATALOG.get(model)
    if info is None:
        return 0.0
    # Anthropic cache pricing: reads at 10% of input, writes at 125% of input.
    # Non-cached input = total prompt minus cache tokens.
    base_input = max(prompt_tokens - cache_read_tokens - cache_creation_tokens, 0)
    return (
        info.cost_per_1k_input * base_input / 1000
        + info.cost_per_1k_input * 0.1 * cache_read_tokens / 1000
        + info.cost_per_1k_input * 1.25 * cache_creation_tokens / 1000
        + info.cost_per_1k_output * completion_tokens / 1000
    )


def _extract_trace_from_headers(headers: httpx.Headers | dict) -> str:
    """Pull a trace ID from common header conventions, or generate one."""
    for key in ("x-trace-id", "x-request-id"):
        val = headers.get(key)
        if val:
            return val
    return uuid.uuid4().hex


def _detect_framework_from_headers(headers: httpx.Headers | dict) -> str | None:
    ua = (headers.get("user-agent") or "").lower()
    for framework, hints in FRAMEWORK_HINTS.items():
        if any(hint in ua for hint in hints):
            return framework
    return None


def _infer_provider(model: str) -> str:
    return infer_provider(model)


def _build_upstream_headers(request_headers: dict) -> dict[str, str]:
    return {
        k: v for k, v in request_headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


def _enqueue(
    queue: asyncio.Queue[LLMEvent],
    event: LLMEvent,
    request: Request | None = None,
    messages: list[dict] | None = None,
    completion: str = "",
    system_prompt: str | list | None = None,
) -> None:
    """Enqueue event for persistence, and optionally for benchmark sampling."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("Proxy event queue full — dropping event %s", event.id)

    # Mirror to benchmark worker when enabled
    if request is not None and messages is not None:
        bench_queue = getattr(request.app.state, "benchmark_queue", None)
        bench_config: BenchmarkConfig | None = getattr(request.app.state, "benchmark_config", None)
        if bench_queue is not None and bench_config is not None:
            if should_sample(event, bench_config, messages=messages):
                try:
                    bench_queue.put_nowait((event, messages, completion, system_prompt))
                except asyncio.QueueFull:
                    logger.warning("Benchmark queue full — skipping event %s", event.id)


def _request_uses_tools(body: dict) -> bool:
    """Detect tool use in an OpenAI-format request (tools array or history)."""
    if body.get("tools") or body.get("functions"):
        return True
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            return True
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            return True
    return False


_PLAN_MODE_MARKERS = ("Plan mode is active", "Plan mode still active")


def _is_plan_mode(body: dict) -> bool:
    """Detect Claude Code plan mode from system prompt content."""
    for msg in body.get("messages", []):
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                return any(marker in content for marker in _PLAN_MODE_MARKERS)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if any(marker in text for marker in _PLAN_MODE_MARKERS):
                            return True
            return False  # Only check first system message
    return False


def _is_plan_mode_anthropic(body: dict) -> bool:
    """Detect Claude Code plan mode from Anthropic-format system field."""
    system = body.get("system", "")
    if isinstance(system, str):
        return any(marker in system for marker in _PLAN_MODE_MARKERS)
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if any(marker in text for marker in _PLAN_MODE_MARKERS):
                    return True
    return False


def _request_uses_tools_anthropic(body: dict) -> bool:
    """Detect tool use in an Anthropic-format request (tools array or history)."""
    if body.get("tools"):
        return True
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                return True
    return False


async def _maybe_route(
    request: Request, body: dict, model: str, classify_fn: Callable[..., Any],
    *, has_tool_use: bool = False, allowed_models: set[str] | None = None,
) -> tuple[str, RoutingDecision | None, tuple[TaskType | None, float | None, str | None]]:
    """Pre-classify and resolve routing if enabled.

    Returns (model, decision, (task_type, confidence, sys_hash)).
    The classification result is returned so callers can reuse it
    instead of re-running the classifier after the upstream response.
    """
    # X-Force-Model header bypasses routing entirely
    forced = request.headers.get("x-force-model")
    if forced:
        return forced, None, (None, None, None)

    # Plan mode → force Opus for strongest reasoning
    if _is_plan_mode(body) or _is_plan_mode_anthropic(body):
        return "claude-opus-4-6", None, (None, None, None)

    routing_enabled: bool = getattr(request.app.state, "routing_enabled", False)
    if not routing_enabled:
        return model, None, (None, None, None)

    fitness_cache: FitnessCache | None = getattr(request.app.state, "fitness_cache", None)
    policy = getattr(request.app.state, "routing_policy", None)
    if fitness_cache is None or policy is None:
        return model, None, (None, None, None)

    # Pre-classify to get task_type (token counts unknown yet, pass 0)
    task_type, confidence, sys_hash = await classify_fn(request, body, 0, 0)
    if task_type is None:
        return model, None, (task_type, confidence, sys_hash)

    # Gate on classifier confidence — only route when classification is trustworthy
    confidence_threshold = getattr(request.app.state, "routing_confidence_threshold", 0.7)
    if confidence is None or confidence < confidence_threshold:
        decision = RoutingDecision(
            selected_model=model,
            reason=f"passthrough: classifier confidence {confidence if confidence is not None else 0:.2f} < {confidence_threshold:.2f}",
            was_overridden=False,
            policy_rule_id=None,
            confidence=confidence,
        )
        record_decision(decision)
        decision_queue = getattr(request.app.state, "decision_queue", None)
        if decision_queue is not None:
            record = DecisionRecord(
                task_type=task_type.value if hasattr(task_type, "value") else str(task_type),
                requested_model=model,
                selected_model=model,
                was_overridden=False,
                reason=decision.reason,
                policy_version=policy.version if hasattr(policy, "version") else None,
                group_name=None,
            )
            try:
                decision_queue.put_nowait(record)
            except asyncio.QueueFull:
                logger.warning("Decision queue full — dropping routing decision")
        return model, decision, (task_type, confidence, sys_hash)

    decision = resolve(
        task_type=task_type,
        requested_model=model,
        fitness_cache=fitness_cache,
        policy=policy,
        has_tool_use=has_tool_use,
        allowed_models=allowed_models,
    )
    decision.confidence = confidence

    # Record decision in the in-memory buffer for the dashboard feed
    record_decision(decision)

    # Enqueue for DB persistence via the RoutingDecisionWriter
    decision_queue = getattr(request.app.state, "decision_queue", None)
    if decision_queue is not None:
        record = DecisionRecord(
            task_type=task_type.value if hasattr(task_type, "value") else str(task_type),
            requested_model=model,
            selected_model=decision.selected_model,
            was_overridden=decision.was_overridden,
            reason=decision.reason,
            policy_version=policy.version if hasattr(policy, "version") else None,
            group_name=decision.group,
        )
        try:
            decision_queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("Decision queue full — dropping routing decision")

    if decision.was_overridden:
        return decision.selected_model, decision, (task_type, confidence, sys_hash)

    return model, decision, (task_type, confidence, sys_hash)


async def _classify_request(
    request: Request,
    body: dict,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[TaskType | None, float | None, str | None]:
    """Run the classifier and extract system prompt hash in one pass.

    Returns (task_type, confidence, system_prompt_hash).
    """
    messages = body.get("messages", [])

    system_prompt_keywords: list[str] = []
    system_prompt_hash: str | None = None
    has_code_fence = False
    has_json_schema = False
    output_format_hint: str | None = None

    user_kw_set: set[str] = set()
    last_user_message: str | None = None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            if system_prompt_hash is None:
                system_prompt_hash = hash_content(content)
            system_prompt_keywords.extend(extract_keywords(content))
            has_code_fence = has_code_fence or "```" in content
            has_json_schema = has_json_schema or ('"type"' in content and '"properties"' in content)
            content_lower = content.lower()
            if output_format_hint is None:
                if "json" in content_lower:
                    output_format_hint = "json"
                elif "```" in content:
                    output_format_hint = "code"
        elif role == "user" and isinstance(content, str):
            user_kw_set.update(extract_keywords(content))
            last_user_message = content

    tools = body.get("tools") or body.get("functions") or []
    token_ratio = compute_token_ratio(prompt_tokens, completion_tokens)

    # Detect tool calls in conversation history (previous assistant turns)
    has_tool_calls = any(
        isinstance(msg, dict)
        and msg.get("role") == "assistant"
        and msg.get("tool_calls")
        for msg in messages
    )

    inp = ClassifierInput(
        system_prompt_hash=system_prompt_hash,
        has_tools=bool(tools),
        tool_count=len(tools),
        has_json_schema=has_json_schema,
        has_code_fence_in_system=has_code_fence,
        prompt_token_count=prompt_tokens,
        completion_token_count=completion_tokens,
        token_ratio=token_ratio,
        model=body.get("model", "unknown"),
        system_prompt_keywords=system_prompt_keywords,
        user_prompt_keywords=list(user_kw_set),
        has_tool_calls=has_tool_calls,
        output_format_hint=output_format_hint,
        last_user_message=last_user_message,
    )
    config = get_config()
    if config.classifier_use_ml:
        try:
            _classifier_api_key = (
                os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            )
            result = await llm_classify(
                inp,
                model=config.classifier_model,
                timeout_s=config.classifier_llm_timeout_s,
                client=request.app.state.http_client,
                api_key=_classifier_api_key,
            )
            return result.task_type, result.confidence, system_prompt_hash
        except Exception as exc:
            logger.warning("LLM classifier failed (%s), falling back to rules", exc)

    result = classify(inp)
    return result.task_type, result.confidence, system_prompt_hash


def _build_event(
    *,
    started_at: datetime,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    body: dict,
    completion_content: str,
    sys_hash: str | None,
    trace_id: str,
    framework: str | None,
    tool_calls: list[ToolCallRecord],
    task_type: TaskType | None,
    task_confidence: float | None,
    response_id: str | None,
    error_type: str | None,
    error_hash: str | None,
    ttft_ms: float | None = None,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> LLMEvent:
    """Single construction site for LLMEvent — avoids 4x copy-paste."""
    return LLMEvent(
        id=uuid.uuid4(),
        created_at=started_at,
        status=EventStatus.FAILURE if error_type else EventStatus.SUCCESS,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=_compute_cost(
            model, prompt_tokens, completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ),
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        latency_ms=latency_ms,
        time_to_first_token_ms=ttft_ms,
        prompt_hash=hash_content(body.get("messages", [])),
        completion_hash=hash_content(completion_content),
        system_prompt_hash=sys_hash,
        trace_id=trace_id,
        span_id=uuid.uuid4().hex,
        agent_framework=framework,
        tool_calls=tool_calls,
        has_tool_calls=len(tool_calls) > 0,
        task_type=task_type,
        task_type_confidence=task_confidence,
        litellm_call_id=response_id or uuid.uuid4().hex,
        error_type=error_type,
        error_message_hash=error_hash,
    )


# ---------------------------------------------------------------------------
# Stream accumulator — collects data across SSE chunks
# ---------------------------------------------------------------------------

class _StreamAccumulator:
    __slots__ = (
        "content_parts", "tool_calls_by_index", "prompt_tokens",
        "completion_tokens", "ttft_ms", "model", "finish_reason",
        "response_id",
    )

    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.tool_calls_by_index: dict[int, dict[str, str]] = {}
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.ttft_ms: float | None = None
        self.model: str | None = None
        self.finish_reason: str | None = None
        self.response_id: str | None = None

    def feed_chunk(self, data: dict[str, Any], elapsed_ms: float) -> None:
        """Ingest one parsed SSE data object."""
        if not self.response_id and data.get("id"):
            self.response_id = data["id"]
        if not self.model and data.get("model"):
            self.model = data["model"]

        for choice in data.get("choices", []):
            delta = choice.get("delta", {})

            # Content deltas
            content = delta.get("content")
            if content:
                if self.ttft_ms is None:
                    self.ttft_ms = elapsed_ms
                self.content_parts.append(content)

            # Tool call deltas — streamed incrementally by index
            for tc_delta in delta.get("tool_calls", []):
                idx = tc_delta.get("index", 0)
                if idx not in self.tool_calls_by_index:
                    self.tool_calls_by_index[idx] = {"name": "", "arguments": ""}
                fn = tc_delta.get("function", {})
                if fn.get("name"):
                    self.tool_calls_by_index[idx]["name"] = fn["name"]
                if fn.get("arguments"):
                    self.tool_calls_by_index[idx]["arguments"] += fn["arguments"]

            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]

        # Token usage from the final chunk (requires stream_options.include_usage)
        usage = data.get("usage")
        if usage:
            self.prompt_tokens = usage.get("prompt_tokens", 0) or 0
            self.completion_tokens = usage.get("completion_tokens", 0) or 0

    @property
    def full_content(self) -> str:
        return "".join(self.content_parts)

    @property
    def tool_call_records(self) -> list[ToolCallRecord]:
        records = []
        for _idx in sorted(self.tool_calls_by_index):
            tc = self.tool_calls_by_index[_idx]
            records.append(ToolCallRecord(
                tool_name=tc["name"] or "unknown",
                args_hash=hash_content(tc["arguments"]),
            ))
        return records


# ---------------------------------------------------------------------------
# Anthropic-native stream accumulator
# ---------------------------------------------------------------------------

class _AnthropicStreamAccumulator:
    """Accumulates Anthropic SSE events (event: X / data: {...}) into an LLMEvent."""

    __slots__ = (
        "content_parts", "tool_calls_by_index", "prompt_tokens",
        "completion_tokens", "cache_read_tokens", "cache_creation_tokens",
        "ttft_ms", "model", "stop_reason", "response_id",
    )

    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.tool_calls_by_index: dict[int, dict[str, str]] = {}
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.cache_read_tokens: int = 0
        self.cache_creation_tokens: int = 0
        self.ttft_ms: float | None = None
        self.model: str | None = None
        self.stop_reason: str | None = None
        self.response_id: str | None = None

    def feed_event(self, event_type: str, data: dict, elapsed_ms: float) -> None:
        if event_type == "message_start":
            msg = data.get("message", {})
            self.response_id = msg.get("id")
            self.model = msg.get("model")
            usage = msg.get("usage", {})
            self.cache_read_tokens = usage.get("cache_read_input_tokens") or 0
            self.cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0
            # Total prompt = all input buckets summed (for analytics/display)
            self.prompt_tokens = (
                (usage.get("input_tokens") or 0)
                + self.cache_read_tokens
                + self.cache_creation_tokens
            )
        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            idx = data.get("index", 0)
            if block.get("type") == "tool_use":
                self.tool_calls_by_index[idx] = {"name": block.get("name", ""), "arguments": ""}
        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            idx = data.get("index", 0)
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    if self.ttft_ms is None:
                        self.ttft_ms = elapsed_ms
                    self.content_parts.append(text)
            elif delta.get("type") == "input_json_delta" and idx in self.tool_calls_by_index:
                self.tool_calls_by_index[idx]["arguments"] += delta.get("partial_json", "")
        elif event_type == "message_delta":
            self.stop_reason = data.get("delta", {}).get("stop_reason")
            self.completion_tokens = data.get("usage", {}).get("output_tokens", 0) or 0

    @property
    def full_content(self) -> str:
        return "".join(self.content_parts)

    @property
    def tool_call_records(self) -> list[ToolCallRecord]:
        return [
            ToolCallRecord(
                tool_name=tc["name"] or "unknown",
                args_hash=hash_content(tc["arguments"]),
            )
            for _idx, tc in sorted(self.tool_calls_by_index.items())
        ]


async def _classify_anthropic_request(
    request: Request,
    body: dict,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[TaskType | None, float | None, str | None]:
    """Wrap _classify_request for Anthropic's message format.

    Anthropic puts system outside of messages; we build a compat body so the
    existing classifier (which looks for role=="system" in messages) can run.
    """
    system = body.get("system", "")
    if isinstance(system, list):
        # content-block array form — join text blocks
        system = " ".join(
            b.get("text", "") for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        )
    compat = {
        "messages": (
            [{"role": "system", "content": system}] if system else []
        ) + body.get("messages", []),
        "tools": body.get("tools", []),
        "model": body.get("model", "unknown"),
    }
    return await _classify_request(request, compat, prompt_tokens, completion_tokens)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/v1/models")
async def proxy_models(request: Request) -> JSONResponse:
    """Passthrough to upstream /v1/models — no event capture."""
    client: httpx.AsyncClient = request.app.state.http_client
    headers = _build_upstream_headers(dict(request.headers))
    resp = await client.get("/v1/models", headers=headers)
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.post("/v1/chat/completions", response_model=None)
async def proxy_chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    client: httpx.AsyncClient = request.app.state.http_client
    headers = _build_upstream_headers(dict(request.headers))
    req_headers = request.headers

    trace_id = _extract_trace_from_headers(req_headers)
    framework = _detect_framework_from_headers(req_headers)
    started_at = utcnow()
    mono_start = time.monotonic()
    model = body.get("model", "unknown")

    # Routing: potentially override the model before forwarding
    routed_model, _, _ = await _maybe_route(
        request, body, model, _classify_request,
        has_tool_use=_request_uses_tools(body),
    )
    original_model = model
    if routed_model != model:
        body["model"] = routed_model
        model = routed_model
        sanitize_for_target(body, source_model=original_model, target_model=routed_model)

    # Always strip params unsupported by the outgoing model (even without routing override).
    # chat/completions upstream is always LiteLLM (localhost:4000).
    strip_unsupported_params(body, model, upstream_is_litellm=True)

    repair_tool_pairing(body)

    if body.get("stream", False):
        return await _handle_streaming(
            client, body, headers, request,
            trace_id, framework, started_at, mono_start, model,
        )
    return await _handle_non_streaming(
        client, body, headers, request,
        trace_id, framework, started_at, mono_start, model,
    )


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------

async def _handle_non_streaming(
    client: httpx.AsyncClient,
    body: dict,
    headers: dict[str, str],
    request: Request,
    trace_id: str,
    framework: str | None,
    started_at: datetime,
    mono_start: float,
    model: str,
) -> JSONResponse:
    resp = await client.post("/v1/chat/completions", json=body, headers=headers)
    latency_ms = (time.monotonic() - mono_start) * 1000

    status_code = resp.status_code
    try:
        data = resp.json()
    except Exception:
        logger.warning("Failed to parse JSON from OpenAI upstream (status=%d)", status_code)
        data = {}

    event_status = EventStatus.SUCCESS if 200 <= status_code < 300 else EventStatus.FAILURE

    # Parse usage + completion content
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0

    completion_content = ""
    tool_calls: list[ToolCallRecord] = []
    for choice in data.get("choices", []):
        msg = choice.get("message", {})
        completion_content = msg.get("content") or ""
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            tool_calls.append(ToolCallRecord(
                tool_name=fn.get("name", "unknown"),
                args_hash=hash_content(fn.get("arguments", "")),
            ))
        break  # first choice only

    # Classify + get system_prompt_hash in one pass
    task_type, task_confidence, sys_hash = await _classify_request(request, body, prompt_tokens, completion_tokens)

    # Error info
    error_type = None
    error_message_hash = None
    if event_status == EventStatus.FAILURE:
        err = data.get("error", {})
        error_type = err.get("type") or f"http_{status_code}"
        error_message_hash = hash_content(err.get("message", ""))

    event = LLMEvent(
        id=uuid.uuid4(),
        created_at=started_at,
        status=event_status,
        provider=_infer_provider(model),
        model=data.get("model", model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=_compute_cost(data.get("model", model), prompt_tokens, completion_tokens),
        latency_ms=latency_ms,
        prompt_hash=hash_content(body.get("messages", [])),
        completion_hash=hash_content(completion_content),
        system_prompt_hash=sys_hash,
        trace_id=trace_id,
        span_id=uuid.uuid4().hex,
        agent_framework=framework,
        tool_calls=tool_calls,
        has_tool_calls=len(tool_calls) > 0,
        task_type=task_type,
        task_type_confidence=task_confidence,
        litellm_call_id=data.get("id", uuid.uuid4().hex),
        error_type=error_type,
        error_message_hash=error_message_hash,
    )
    queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
    _enqueue(queue, event, request=request, messages=body.get("messages", []), completion=completion_content, system_prompt=None)

    return JSONResponse(content=data, status_code=status_code)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _handle_streaming(
    client: httpx.AsyncClient,
    body: dict,
    headers: dict[str, str],
    request: Request,
    trace_id: str,
    framework: str | None,
    started_at: datetime,
    mono_start: float,
    model: str,
) -> StreamingResponse:
    # Inject stream_options so final chunk contains token usage
    body.setdefault("stream_options", {})["include_usage"] = True

    upstream_req = client.build_request(
        "POST", "/v1/chat/completions", json=body, headers=headers,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    acc = _StreamAccumulator()
    stream_error: Exception | None = None

    async def _generate():
        nonlocal stream_error
        try:
            async for line in upstream_resp.aiter_lines():
                yield line + "\n"

                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                elapsed = (time.monotonic() - mono_start) * 1000
                acc.feed_chunk(chunk, elapsed)
        except Exception as exc:
            stream_error = exc
            logger.warning("Upstream stream failed: %s", exc)
        finally:
            await upstream_resp.aclose()

            latency_ms = (time.monotonic() - mono_start) * 1000
            resolved_model = acc.model or model
            prompt_tokens = acc.prompt_tokens
            completion_tokens = acc.completion_tokens
            tool_calls = acc.tool_call_records

            task_type, task_confidence, sys_hash = await _classify_request(
                request, body, prompt_tokens, completion_tokens,
            )

            status = EventStatus.FAILURE if stream_error else EventStatus.SUCCESS
            err_type = type(stream_error).__name__ if stream_error else None
            err_hash = hash_content(str(stream_error)) if stream_error else None

            event = LLMEvent(
                id=uuid.uuid4(),
                created_at=started_at,
                status=status,
                provider=_infer_provider(resolved_model),
                model=resolved_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost=_compute_cost(resolved_model, prompt_tokens, completion_tokens),
                latency_ms=latency_ms,
                time_to_first_token_ms=acc.ttft_ms,
                prompt_hash=hash_content(body.get("messages", [])),
                completion_hash=hash_content(acc.full_content),
                system_prompt_hash=sys_hash,
                trace_id=trace_id,
                span_id=uuid.uuid4().hex,
                agent_framework=framework,
                tool_calls=tool_calls,
                has_tool_calls=len(tool_calls) > 0,
                task_type=task_type,
                task_type_confidence=task_confidence,
                litellm_call_id=acc.response_id or uuid.uuid4().hex,
                error_type=err_type,
                error_message_hash=err_hash,
            )
            queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
            _enqueue(queue, event, request=request, messages=body.get("messages", []), completion=acc.full_content, system_prompt=None)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        status_code=upstream_resp.status_code,
    )


# ---------------------------------------------------------------------------
# Anthropic /v1/messages/count_tokens — transparent passthrough (no capture)
# ---------------------------------------------------------------------------

@router.post("/v1/messages/count_tokens", response_model=None)
async def proxy_count_tokens(request: Request) -> JSONResponse:
    """Forward token-counting requests to the upstream as-is.

    No event capture needed — this is a read-only estimation call.
    """
    client: httpx.AsyncClient = request.app.state.anthropic_client
    headers = _build_upstream_headers(dict(request.headers))
    query_string = str(request.url.query)
    path = f"/v1/messages/count_tokens?{query_string}" if query_string else "/v1/messages/count_tokens"
    body = await request.json()
    upstream_req = client.build_request("POST", path, json=body, headers=headers)
    upstream_resp = await client.send(upstream_req)
    data = upstream_resp.json()
    return JSONResponse(content=data, status_code=upstream_resp.status_code)


# ---------------------------------------------------------------------------
# Anthropic /v1/messages — non-streaming + streaming
# ---------------------------------------------------------------------------

@router.post("/v1/messages", response_model=None)
async def proxy_messages(request: Request) -> JSONResponse | StreamingResponse:
    """Proxy Anthropic-native /v1/messages requests with event capture.

    Claude Code (and other Anthropic SDK clients) use this endpoint instead of
    /v1/chat/completions.  Query params (e.g. ?beta=true) are forwarded as-is.
    """
    body = await request.json()
    client: httpx.AsyncClient = request.app.state.anthropic_client
    headers = _build_upstream_headers(dict(request.headers))

    trace_id = _extract_trace_from_headers(request.headers)
    framework = _detect_framework_from_headers(request.headers)
    started_at = utcnow()
    mono_start = time.monotonic()
    model = body.get("model", "unknown")
    query_string = str(request.url.query)

    # Routing: constrain to models actually available upstream.
    # When LiteLLM is present, upstream_models lists everything it can serve.
    # When direct-to-Anthropic (no LiteLLM /v1/models), fall back to
    # Anthropic-only models since non-Anthropic would have no upstream.
    upstream_models: set[str] | None = getattr(request.app.state, "upstream_models", None)
    if upstream_models is None:
        upstream_models = get_anthropic_models()
    routed_model, _, _ = await _maybe_route(
        request, body, model, _classify_anthropic_request,
        has_tool_use=_request_uses_tools_anthropic(body),
        allowed_models=upstream_models,
    )
    original_model = model
    if routed_model != model:
        body["model"] = routed_model
        model = routed_model
        sanitize_for_target(body, source_model=original_model, target_model=routed_model)

    # Always strip params unsupported by the outgoing model (even without routing override).
    # Remote LiteLLM can re-route (e.g. opus→haiku) without stripping effort.
    upstream_is_litellm = getattr(request.app.state, "anthropic_upstream_is_litellm", False)
    strip_unsupported_params(body, model, upstream_is_litellm=upstream_is_litellm)

    # Always repair tool pairing — even without routing override, LiteLLM
    # may route to OpenAI via model groups, which rejects orphaned tool_calls
    repair_tool_pairing(body)

    # Non-Anthropic models: convert to OpenAI format and use /v1/chat/completions
    # to bypass LiteLLM's broken Anthropic→OpenAI message translator
    target_provider = infer_provider(model)
    if target_provider != "anthropic":
        openai_client: httpx.AsyncClient = request.app.state.http_client
        if body.get("stream", False):
            return await _handle_messages_via_openai_streaming(
                openai_client, body, headers, request,
                trace_id, framework, started_at, mono_start, model,
            )
        return await _handle_messages_via_openai(
            openai_client, body, headers, request,
            trace_id, framework, started_at, mono_start, model,
        )

    if body.get("stream", False):
        return await _handle_messages_streaming(
            client, body, headers, query_string, request,
            trace_id, framework, started_at, mono_start, model,
        )
    return await _handle_messages_non_streaming(
        client, body, headers, query_string, request,
        trace_id, framework, started_at, mono_start, model,
    )


async def _handle_messages_non_streaming(
    client: httpx.AsyncClient,
    body: dict,
    headers: dict[str, str],
    query_string: str,
    request: Request,
    trace_id: str,
    framework: str | None,
    started_at: datetime,
    mono_start: float,
    model: str,
) -> JSONResponse:
    path = f"/v1/messages?{query_string}" if query_string else "/v1/messages"
    resp = await client.post(path, json=body, headers=headers)
    latency_ms = (time.monotonic() - mono_start) * 1000

    status_code = resp.status_code
    try:
        data = resp.json()
    except Exception:
        logger.warning("Failed to parse JSON from Anthropic upstream (status=%d)", status_code)
        data = {}

    event_status = EventStatus.SUCCESS if 200 <= status_code < 300 else EventStatus.FAILURE

    usage = data.get("usage", {})
    cache_read_tokens = usage.get("cache_read_input_tokens") or 0
    cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0
    prompt_tokens = (
        (usage.get("input_tokens") or 0)
        + cache_read_tokens
        + cache_creation_tokens
    )
    completion_tokens = usage.get("output_tokens", 0) or 0

    completion_content = ""
    tool_calls: list[ToolCallRecord] = []
    for block in data.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            completion_content += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append(ToolCallRecord(
                tool_name=block.get("name", "unknown"),
                args_hash=hash_content(json.dumps(block.get("input", {}))),
            ))

    task_type, task_confidence, sys_hash = await _classify_anthropic_request(
        request, body, prompt_tokens, completion_tokens,
    )

    error_type = None
    error_message_hash = None
    if event_status == EventStatus.FAILURE:
        err = data.get("error", {})
        error_type = err.get("type") or f"http_{status_code}"
        error_message_hash = hash_content(err.get("message", ""))

    event = LLMEvent(
        id=uuid.uuid4(),
        created_at=started_at,
        status=event_status,
        provider="anthropic",
        model=data.get("model", model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=_compute_cost(
            data.get("model", model), prompt_tokens, completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ),
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        latency_ms=latency_ms,
        prompt_hash=hash_content(body.get("messages", [])),
        completion_hash=hash_content(completion_content),
        system_prompt_hash=sys_hash,
        trace_id=trace_id,
        span_id=uuid.uuid4().hex,
        agent_framework=framework,
        tool_calls=tool_calls,
        has_tool_calls=len(tool_calls) > 0,
        task_type=task_type,
        task_type_confidence=task_confidence,
        litellm_call_id=data.get("id", uuid.uuid4().hex),
        error_type=error_type,
        error_message_hash=error_message_hash,
    )
    queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
    _enqueue(queue, event, request=request, messages=body.get("messages", []), completion=completion_content, system_prompt=body.get("system"))

    return JSONResponse(content=data, status_code=status_code)


async def _handle_messages_streaming(
    client: httpx.AsyncClient,
    body: dict,
    headers: dict[str, str],
    query_string: str,
    request: Request,
    trace_id: str,
    framework: str | None,
    started_at: datetime,
    mono_start: float,
    model: str,
) -> StreamingResponse:
    path = f"/v1/messages?{query_string}" if query_string else "/v1/messages"
    upstream_req = client.build_request("POST", path, json=body, headers=headers)
    upstream_resp = await client.send(upstream_req, stream=True)

    acc = _AnthropicStreamAccumulator()
    stream_error: Exception | None = None

    async def _generate():
        nonlocal stream_error
        current_event_type: str | None = None
        try:
            async for line in upstream_resp.aiter_lines():
                yield line + "\n"

                if line.startswith("event: "):
                    current_event_type = line[7:].strip()
                elif line.startswith("data: ") and current_event_type:
                    try:
                        chunk = json.loads(line[6:])
                        elapsed = (time.monotonic() - mono_start) * 1000
                        acc.feed_event(current_event_type, chunk, elapsed)
                    except json.JSONDecodeError:
                        pass
        except Exception as exc:
            stream_error = exc
            logger.warning("Upstream stream failed: %s", exc)
        finally:
            await upstream_resp.aclose()

            latency_ms = (time.monotonic() - mono_start) * 1000
            resolved_model = acc.model or model
            tool_calls = acc.tool_call_records

            task_type, task_confidence, sys_hash = await _classify_anthropic_request(
                request, body, acc.prompt_tokens, acc.completion_tokens,
            )

            status = EventStatus.FAILURE if stream_error else EventStatus.SUCCESS
            event = LLMEvent(
                id=uuid.uuid4(),
                created_at=started_at,
                status=status,
                provider="anthropic",
                model=resolved_model,
                prompt_tokens=acc.prompt_tokens,
                completion_tokens=acc.completion_tokens,
                total_tokens=acc.prompt_tokens + acc.completion_tokens,
                estimated_cost=_compute_cost(
                    resolved_model, acc.prompt_tokens, acc.completion_tokens,
                    cache_read_tokens=acc.cache_read_tokens,
                    cache_creation_tokens=acc.cache_creation_tokens,
                ),
                cache_read_tokens=acc.cache_read_tokens,
                cache_creation_tokens=acc.cache_creation_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=acc.ttft_ms,
                prompt_hash=hash_content(body.get("messages", [])),
                completion_hash=hash_content(acc.full_content),
                system_prompt_hash=sys_hash,
                trace_id=trace_id,
                span_id=uuid.uuid4().hex,
                agent_framework=framework,
                tool_calls=tool_calls,
                has_tool_calls=len(tool_calls) > 0,
                task_type=task_type,
                task_type_confidence=task_confidence,
                litellm_call_id=acc.response_id or uuid.uuid4().hex,
                error_type=type(stream_error).__name__ if stream_error else None,
                error_message_hash=hash_content(str(stream_error)) if stream_error else None,
            )
            queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
            _enqueue(queue, event, request=request, messages=body.get("messages", []), completion=acc.full_content, system_prompt=body.get("system"))

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        status_code=upstream_resp.status_code,
    )


# ---------------------------------------------------------------------------
# Anthropic /v1/messages → OpenAI /v1/chat/completions bridge
#
# When a /v1/messages request targets a non-Anthropic model, we convert the
# messages ourselves and route through /v1/chat/completions, then convert
# the response back to Anthropic format.  This bypasses LiteLLM's broken
# Anthropic→OpenAI message translator which mangles tool_use/tool_result.
# ---------------------------------------------------------------------------

async def _handle_messages_via_openai(
    client: httpx.AsyncClient,
    anthropic_body: dict,
    headers: dict[str, str],
    request: Request,
    trace_id: str,
    framework: str | None,
    started_at: datetime,
    mono_start: float,
    model: str,
) -> JSONResponse:
    openai_body = anthropic_to_openai_request(anthropic_body)
    resp = await client.post("/v1/chat/completions", json=openai_body, headers=headers)
    latency_ms = (time.monotonic() - mono_start) * 1000

    status_code = resp.status_code
    try:
        data = resp.json()
    except Exception:
        logger.warning("Failed to parse JSON from OpenAI upstream (status=%d)", status_code)
        data = {}

    # Convert response back to Anthropic format for Claude Code
    if 200 <= status_code < 300:
        anthropic_resp = openai_to_anthropic_response(data, model=model)
    else:
        err = data.get("error", {})
        anthropic_resp = {
            "type": "error",
            "error": {
                "type": err.get("type", f"http_{status_code}"),
                "message": err.get("message", "Unknown error"),
            },
        }

    # Capture event
    event_status = EventStatus.SUCCESS if 200 <= status_code < 300 else EventStatus.FAILURE
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0

    completion_content = ""
    tool_calls: list[ToolCallRecord] = []
    for choice in data.get("choices", []):
        msg = choice.get("message", {})
        completion_content = msg.get("content") or ""
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            tool_calls.append(ToolCallRecord(
                tool_name=fn.get("name", "unknown"),
                args_hash=hash_content(fn.get("arguments", "")),
            ))
        break

    task_type, task_confidence, sys_hash = await _classify_anthropic_request(
        request, anthropic_body, prompt_tokens, completion_tokens,
    )

    error_type = None
    error_message_hash = None
    if event_status == EventStatus.FAILURE:
        err = data.get("error", {})
        error_type = err.get("type") or f"http_{status_code}"
        error_message_hash = hash_content(err.get("message", ""))

    event = _build_event(
        started_at=started_at,
        model=data.get("model", model),
        provider=_infer_provider(model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        body=anthropic_body,
        completion_content=completion_content,
        sys_hash=sys_hash,
        trace_id=trace_id,
        framework=framework,
        tool_calls=tool_calls,
        task_type=task_type,
        task_confidence=task_confidence,
        response_id=data.get("id"),
        error_type=error_type,
        error_hash=error_message_hash,
    )
    queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
    _enqueue(queue, event, request=request, messages=anthropic_body.get("messages", []),
             completion=completion_content, system_prompt=anthropic_body.get("system"))

    return JSONResponse(content=anthropic_resp, status_code=status_code)


async def _handle_messages_via_openai_streaming(
    client: httpx.AsyncClient,
    anthropic_body: dict,
    headers: dict[str, str],
    request: Request,
    trace_id: str,
    framework: str | None,
    started_at: datetime,
    mono_start: float,
    model: str,
) -> StreamingResponse:
    openai_body = anthropic_to_openai_request(anthropic_body)
    openai_body["stream"] = True
    openai_body.setdefault("stream_options", {})["include_usage"] = True

    upstream_req = client.build_request(
        "POST", "/v1/chat/completions", json=openai_body, headers=headers,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    acc = _StreamAccumulator()
    stream_error: Exception | None = None
    block_idx = 0

    async def _generate():
        nonlocal stream_error, block_idx

        # Anthropic stream preamble
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"

        text_block_started = False
        try:
            async for line in upstream_resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                elapsed = (time.monotonic() - mono_start) * 1000
                acc.feed_chunk(chunk, elapsed)

                # Convert OpenAI deltas → Anthropic SSE events
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})

                    text = delta.get("content")
                    if text:
                        if not text_block_started:
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                            text_block_started = True
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_idx, 'delta': {'type': 'text_delta', 'text': text}})}\n\n"

                    for tc_delta in delta.get("tool_calls", []):
                        fn = tc_delta.get("function", {})
                        if fn.get("name"):
                            if text_block_started:
                                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n"
                                block_idx += 1
                                text_block_started = False
                            tc_id = tc_delta.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'tool_use', 'id': tc_id, 'name': fn['name'], 'input': {}}})}\n\n"
                        if fn.get("arguments"):
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_idx, 'delta': {'type': 'input_json_delta', 'partial_json': fn['arguments']}})}\n\n"

                    finish = choice.get("finish_reason")
                    if finish:
                        if text_block_started or block_idx > 0 or acc.tool_calls_by_index:
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n"

                usage = chunk.get("usage")
                if usage:
                    stop = "tool_use" if acc.tool_calls_by_index else "end_turn"
                    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': usage.get('completion_tokens', 0) or 0}})}\n\n"

        except Exception as exc:
            stream_error = exc
            logger.warning("Upstream stream failed: %s", exc)
        finally:
            await upstream_resp.aclose()

            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

            latency_ms = (time.monotonic() - mono_start) * 1000
            resolved_model = acc.model or model
            task_type, task_confidence, sys_hash = await _classify_anthropic_request(
                request, anthropic_body, acc.prompt_tokens, acc.completion_tokens,
            )

            status = EventStatus.FAILURE if stream_error else EventStatus.SUCCESS
            event = _build_event(
                started_at=started_at,
                model=resolved_model,
                provider=_infer_provider(resolved_model),
                prompt_tokens=acc.prompt_tokens,
                completion_tokens=acc.completion_tokens,
                latency_ms=latency_ms,
                body=anthropic_body,
                completion_content=acc.full_content,
                sys_hash=sys_hash,
                trace_id=trace_id,
                framework=framework,
                tool_calls=acc.tool_call_records,
                task_type=task_type,
                task_confidence=task_confidence,
                response_id=acc.response_id,
                error_type=type(stream_error).__name__ if stream_error else None,
                error_hash=hash_content(str(stream_error)) if stream_error else None,
                ttft_ms=acc.ttft_ms,
            )
            queue: asyncio.Queue[LLMEvent] = request.app.state.event_queue
            _enqueue(queue, event, request=request, messages=anthropic_body.get("messages", []),
                     completion=acc.full_content, system_prompt=anthropic_body.get("system"))

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        status_code=upstream_resp.status_code,
    )
