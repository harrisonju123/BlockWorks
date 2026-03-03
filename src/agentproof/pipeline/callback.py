"""LiteLLM custom callback handler for AgentProof.

Captures every LLM request/response, hashes content, classifies
the task type, and writes events to TimescaleDB via an async
background buffer. The callback itself never blocks the LLM
request path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from agentproof.classifier.rules import classify
from agentproof.classifier.taxonomy import ClassifierInput
from agentproof.pipeline.context import detect_agent_framework, extract_trace_context
from agentproof.pipeline.hasher import hash_content
from agentproof.pipeline.writer import EventWriter
from agentproof.types import EventStatus, LLMEvent, ToolCallRecord

logger = logging.getLogger(__name__)


class AgentProofCallback(CustomLogger):
    """Async callback that captures LLM events to TimescaleDB.

    DB writes happen via a background task draining an in-memory
    queue. The callback methods only enqueue — never await IO.
    """

    def __init__(
        self,
        db_url: str,
        org_id: str | None = None,
        enable_classification: bool = True,
        batch_size: int = 50,
        flush_interval_ms: int = 100,
    ) -> None:
        self._db_url = db_url
        self._org_id = org_id
        self._enable_classification = enable_classification
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_ms / 1000.0
        self._queue: asyncio.Queue[LLMEvent] = asyncio.Queue(maxsize=10_000)
        self._writer: EventWriter | None = None
        self._writer_task: asyncio.Task | None = None
        self._init_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_writer(self) -> None:
        """Lazily start the background writer on first event."""
        async with self._init_lock:
            if self._writer_task is None:
                self._writer = EventWriter(
                    db_url=self._db_url,
                    queue=self._queue,
                    batch_size=self._batch_size,
                    flush_interval_s=self._flush_interval_s,
                )
                self._writer_task = asyncio.create_task(self._writer.run())

    async def _log_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
        status: EventStatus,
    ) -> None:
        await self._ensure_writer()
        event = self._build_event(kwargs, response_obj, start_time, end_time, status)
        self._enqueue(event)

    async def async_log_success_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await self._log_event(kwargs, response_obj, start_time, end_time, EventStatus.SUCCESS)

    async def async_log_failure_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await self._log_event(kwargs, response_obj, start_time, end_time, EventStatus.FAILURE)

    def _enqueue(self, event: LLMEvent) -> None:
        """Non-blocking enqueue. Drops if buffer is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("AgentProof event queue full — dropping event")

    def _build_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
        status: EventStatus,
    ) -> LLMEvent:
        """Transform LiteLLM callback args into our canonical LLMEvent."""
        litellm_params = kwargs.get("litellm_params", {})
        metadata = litellm_params.get("metadata", {})
        usage = getattr(response_obj, "usage", None)

        # Token counts
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Cost from LiteLLM's calculator
        estimated_cost = kwargs.get("response_cost", 0.0) or 0.0

        # Latency
        latency_ms = (end_time - start_time).total_seconds() * 1000

        # Content hashing
        messages = kwargs.get("messages", [])
        prompt_hash = hash_content(messages)

        # Extract response message once
        completion_content = ""
        tool_calls: list[ToolCallRecord] = []
        choices = getattr(response_obj, "choices", [])
        message = getattr(choices[0], "message", None) if choices else None

        if message:
            completion_content = getattr(message, "content", "") or ""
            raw_tool_calls = getattr(message, "tool_calls", None) or []
            for tc in raw_tool_calls:
                func = getattr(tc, "function", None)
                if func:
                    tool_calls.append(
                        ToolCallRecord(
                            tool_name=getattr(func, "name", "unknown"),
                            args_hash=hash_content(getattr(func, "arguments", "")),
                        )
                    )

        completion_hash = hash_content(completion_content)

        # System prompt hash (first system message if present)
        system_prompt_hash = None
        system_prompt_keywords: list[str] = []
        has_code_fence = False
        has_json_schema = False
        output_format_hint: str | None = None

        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content", "")
                system_prompt_hash = hash_content(content)
                content_lower = content.lower()
                # Extract classification signals from system prompt
                for kw in [
                    "classify", "categorize", "label", "sentiment", "detect",
                    "identify", "summarize", "summary", "tldr", "condense",
                    "extract", "parse", "implement", "function", "class",
                    "refactor", "write code", "explain", "reason", "analyze",
                ]:
                    if kw in content_lower:
                        system_prompt_keywords.append(kw)
                has_code_fence = "```" in content
                has_json_schema = '"type"' in content and '"properties"' in content
                if "json" in content_lower:
                    output_format_hint = "json"
                elif "```" in content:
                    output_format_hint = "code"
                break

        # Trace context
        trace_ctx = extract_trace_context(kwargs)

        # Agent framework detection
        framework, agent_name = detect_agent_framework(kwargs)

        # Classification
        task_type = None
        task_type_confidence = None
        if self._enable_classification:
            token_ratio = (
                completion_tokens / prompt_tokens if prompt_tokens > 0 else 0.0
            )
            has_tools = bool(kwargs.get("tools") or kwargs.get("functions"))
            tool_count = len(kwargs.get("tools", []) or kwargs.get("functions", []))

            classifier_input = ClassifierInput(
                system_prompt_hash=system_prompt_hash,
                has_tools=has_tools,
                tool_count=tool_count,
                has_json_schema=has_json_schema,
                has_code_fence_in_system=has_code_fence,
                prompt_token_count=prompt_tokens,
                completion_token_count=completion_tokens,
                token_ratio=token_ratio,
                model=kwargs.get("model", "unknown"),
                system_prompt_keywords=system_prompt_keywords,
                output_format_hint=output_format_hint,
            )
            result = classify(classifier_input)
            task_type = result.task_type
            task_type_confidence = result.confidence

        # Error info for failures
        error_type = None
        error_message_hash = None
        if status == EventStatus.FAILURE:
            exception = kwargs.get("exception")
            if exception:
                error_type = type(exception).__name__
                error_message_hash = hash_content(str(exception))

        return LLMEvent(
            id=uuid.uuid4(),
            created_at=end_time.astimezone(timezone.utc),
            status=status,
            provider=litellm_params.get("custom_llm_provider", "unknown"),
            model=kwargs.get("model", "unknown"),
            model_group=kwargs.get("model_group"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost=estimated_cost,
            latency_ms=latency_ms,
            prompt_hash=prompt_hash,
            completion_hash=completion_hash,
            system_prompt_hash=system_prompt_hash,
            session_id=trace_ctx["session_id"],
            trace_id=trace_ctx["trace_id"],
            span_id=trace_ctx["span_id"],
            parent_span_id=trace_ctx["parent_span_id"],
            agent_framework=framework,
            agent_name=agent_name,
            tool_calls=tool_calls,
            has_tool_calls=len(tool_calls) > 0,
            task_type=task_type,
            task_type_confidence=task_type_confidence,
            litellm_call_id=kwargs.get("litellm_call_id", ""),
            api_base=litellm_params.get("api_base"),
            org_id=self._org_id or metadata.get("org_id"),
            user_id=metadata.get("user_id"),
            custom_metadata=metadata.get("custom_metadata"),
        )
