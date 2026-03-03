"""Decorators and context managers for automatic LLM call tracking.

Provides zero-config instrumentation for common patterns:
- @track_llm_call: wraps functions that return LLM responses
- agentproof_trace: groups multiple calls under a shared trace
- track_openai / track_anthropic: monkey-patch provider clients
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from typing import Any, ParamSpec, TypeVar

from agentproof.sdk.types import TraceContext
from agentproof.utils import utcnow

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

# Contextvar holding the current trace context so nested calls
# within a `with agentproof_trace(...)` block share the same trace_id.
_active_trace: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "_active_trace", default=None
)


def _get_active_trace() -> TraceContext | None:
    return _active_trace.get()


def track_llm_call(
    client: Any = None,
    *,
    model: str | None = None,
    provider: str = "custom",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that wraps a function making LLM calls and reports metrics.

    The wrapped function must return a dict-like object with at least:
    - "completion" or "content": the response text
    - "prompt_tokens", "completion_tokens": token counts
    - "cost" or "estimated_cost": dollar cost

    If these fields aren't present, the decorator silently skips tracking
    rather than raising — instrumentation should never break the caller.

    Usage:
        @track_llm_call(model="gpt-4o")
        def my_llm_call(prompt: str) -> dict:
            return openai.chat.completions.create(...)
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                start = time.monotonic()
                result = await func(*args, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                try:
                    _record_from_result(
                        result,
                        client=client,
                        model_hint=model,
                        provider=provider,
                        latency_ms=elapsed_ms,
                    )
                except Exception:
                    logger.debug("track_llm_call: could not extract metrics", exc_info=True)
                return result
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(func)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                start = time.monotonic()
                result = func(*args, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                try:
                    _record_from_result(
                        result,
                        client=client,
                        model_hint=model,
                        provider=provider,
                        latency_ms=elapsed_ms,
                    )
                except Exception:
                    logger.debug("track_llm_call: could not extract metrics", exc_info=True)
                return result
            return wrapper
    return decorator


def _record_from_result(
    result: Any,
    *,
    client: Any,
    model_hint: str | None,
    provider: str,
    latency_ms: float,
) -> None:
    """Extract metrics from a result dict/object and enqueue for tracking.

    This is best-effort: if the result doesn't have the expected shape,
    we log at debug level and move on.
    """
    if isinstance(result, dict):
        data = result
    elif hasattr(result, "__dict__"):
        data = result.__dict__
    else:
        return

    completion = data.get("completion") or data.get("content") or ""
    prompt_tokens = data.get("prompt_tokens", 0)
    completion_tokens = data.get("completion_tokens", 0)
    cost = data.get("cost") or data.get("estimated_cost") or 0.0
    resolved_model = data.get("model") or model_hint or "unknown"

    trace_ctx = _get_active_trace()
    event_id = str(uuid.uuid4())

    event_data = {
        "event_id": event_id,
        "model": resolved_model,
        "provider": provider,
        "completion": str(completion),
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "estimated_cost": float(cost),
        "latency_ms": latency_ms,
        "tracked_at": utcnow().isoformat(),
    }

    if trace_ctx:
        event_data["trace_id"] = trace_ctx.trace_id
        event_data["session_id"] = trace_ctx.session_id
        trace_ctx.events.append(event_id)

    # If a client was provided, try to report via its track method
    if client is not None and hasattr(client, "track"):
        try:
            client.track(
                model=resolved_model,
                messages=[],
                completion=str(completion),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                estimated_cost=float(cost),
                latency_ms=latency_ms,
                provider=provider,
                trace_id=trace_ctx.trace_id if trace_ctx else None,
                session_id=trace_ctx.session_id if trace_ctx else None,
            )
        except Exception:
            logger.debug("track_llm_call: failed to report to client", exc_info=True)


@contextmanager
def agentproof_trace(session_id: str, *, trace_id: str | None = None):
    """Context manager that groups LLM calls under a shared trace.

    All @track_llm_call-decorated functions invoked within the block
    will share the same trace_id, making it easy to correlate a multi-step
    agent workflow in the AgentProof dashboard.

    Usage:
        with agentproof_trace("user-session-42") as trace:
            result1 = my_planner_call(prompt)
            result2 = my_executor_call(result1)
            print(f"Trace {trace.trace_id} recorded {len(trace.events)} events")
    """
    ctx = TraceContext(
        session_id=session_id,
        trace_id=trace_id or str(uuid.uuid4()),
        events=[],
        started_at=utcnow(),
    )
    token = _active_trace.set(ctx)
    try:
        yield ctx
    finally:
        _active_trace.reset(token)


def track_openai(
    openai_client: Any,
    *,
    agentproof_client: Any = None,
) -> Any:
    """Monkey-patch an OpenAI client to auto-report completions.

    Wraps the `chat.completions.create` method so every call is
    automatically tracked. The original client is returned (mutated).

    Usage:
        import openai
        client = openai.OpenAI()
        track_openai(client, agentproof_client=ap_client)
        # Now every client.chat.completions.create(...) is tracked
    """
    chat_completions = getattr(openai_client, "chat", None)
    if chat_completions is None:
        logger.warning("track_openai: client has no .chat attribute, skipping")
        return openai_client

    completions = getattr(chat_completions, "completions", None)
    if completions is None:
        logger.warning("track_openai: client.chat has no .completions, skipping")
        return openai_client

    original_create = completions.create

    @functools.wraps(original_create)
    def patched_create(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        result = original_create(*args, **kwargs)
        elapsed_ms = (time.monotonic() - start) * 1000

        try:
            _report_openai_result(result, elapsed_ms, kwargs, agentproof_client)
        except Exception:
            logger.debug("track_openai: failed to report", exc_info=True)

        return result

    completions.create = patched_create
    return openai_client


def _report_openai_result(
    result: Any,
    latency_ms: float,
    kwargs: dict,
    ap_client: Any,
) -> None:
    """Extract metrics from an OpenAI ChatCompletion and report them."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return

    choices = getattr(result, "choices", [])
    content = ""
    if choices:
        message = getattr(choices[0], "message", None)
        if message:
            content = getattr(message, "content", "") or ""

    model = getattr(result, "model", kwargs.get("model", "unknown"))
    prompt_tokens = getattr(usage, "prompt_tokens", 0)
    completion_tokens = getattr(usage, "completion_tokens", 0)

    trace_ctx = _get_active_trace()

    if ap_client is not None and hasattr(ap_client, "track"):
        ap_client.track(
            model=model,
            messages=kwargs.get("messages", []),
            completion=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=0.0,  # Cost calculated server-side
            latency_ms=latency_ms,
            provider="openai",
            trace_id=trace_ctx.trace_id if trace_ctx else None,
            session_id=trace_ctx.session_id if trace_ctx else None,
        )


def track_anthropic(
    anthropic_client: Any,
    *,
    agentproof_client: Any = None,
) -> Any:
    """Monkey-patch an Anthropic client to auto-report completions.

    Wraps the `messages.create` method so every call is automatically
    tracked. The original client is returned (mutated).

    Usage:
        import anthropic
        client = anthropic.Anthropic()
        track_anthropic(client, agentproof_client=ap_client)
        # Now every client.messages.create(...) is tracked
    """
    messages = getattr(anthropic_client, "messages", None)
    if messages is None:
        logger.warning("track_anthropic: client has no .messages attribute, skipping")
        return anthropic_client

    original_create = messages.create

    @functools.wraps(original_create)
    def patched_create(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        result = original_create(*args, **kwargs)
        elapsed_ms = (time.monotonic() - start) * 1000

        try:
            _report_anthropic_result(result, elapsed_ms, kwargs, agentproof_client)
        except Exception:
            logger.debug("track_anthropic: failed to report", exc_info=True)

        return result

    messages.create = patched_create
    return anthropic_client


def _report_anthropic_result(
    result: Any,
    latency_ms: float,
    kwargs: dict,
    ap_client: Any,
) -> None:
    """Extract metrics from an Anthropic Message and report them."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return

    # Anthropic returns content as a list of blocks
    content_blocks = getattr(result, "content", [])
    content = ""
    if content_blocks and hasattr(content_blocks[0], "text"):
        content = content_blocks[0].text

    model = getattr(result, "model", kwargs.get("model", "unknown"))
    prompt_tokens = getattr(usage, "input_tokens", 0)
    completion_tokens = getattr(usage, "output_tokens", 0)

    # Build messages list matching the API format
    messages = kwargs.get("messages", [])
    system = kwargs.get("system")
    if system:
        messages = [{"role": "system", "content": system}] + list(messages)

    trace_ctx = _get_active_trace()

    if ap_client is not None and hasattr(ap_client, "track"):
        ap_client.track(
            model=model,
            messages=messages,
            completion=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=0.0,  # Cost calculated server-side
            latency_ms=latency_ms,
            provider="anthropic",
            trace_id=trace_ctx.trace_id if trace_ctx else None,
            session_id=trace_ctx.session_id if trace_ctx else None,
        )
