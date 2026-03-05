"""LiteLLM custom callback handler for BlockThrough.

Captures every LLM request/response, hashes content, classifies
the task type, and writes events to TimescaleDB via an async
background buffer. The callback itself never blocks the LLM
request path.

When MCP tracing is enabled, tool_use blocks are additionally
parsed for MCP server calls and queued to a dedicated MCPWriter.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from blockthrough.classifier.rules import classify, compute_token_ratio, extract_keywords
from blockthrough.classifier.taxonomy import ClassifierInput
from blockthrough.mcp.extractor import (
    build_execution_graph,
    extract_mcp_calls,
    extract_mcp_calls_from_tool_calls,
)
from blockthrough.mcp.types import MCPCall, MCPExecutionEdge
from blockthrough.mcp.writer import MCPWriter
from blockthrough.pipeline.context import detect_agent_framework, extract_trace_context
from blockthrough.pipeline.hasher import hash_content
from blockthrough.pipeline.writer import EventWriter
from blockthrough.types import EventStatus, LLMEvent, ToolCallRecord

logger = logging.getLogger(__name__)


class BlockThroughCallback(CustomLogger):
    """Async callback that captures LLM events to TimescaleDB.

    DB writes happen via a background task draining an in-memory
    queue. The callback methods only enqueue — never await IO.
    """

    def __init__(
        self,
        db_url: str | None = None,
        org_id: str | None = None,
        enable_classification: bool | None = None,
        mcp_tracing_enabled: bool | None = None,
        batch_size: int | None = None,
        flush_interval_ms: int | None = None,
    ) -> None:
        # When LiteLLM instantiates with no args, pull from config
        from blockthrough.config import get_config
        cfg = get_config()

        self._db_url = db_url or cfg.database_url
        self._org_id = org_id or cfg.org_id
        self._enable_classification = enable_classification if enable_classification is not None else cfg.pipeline_enable_classification
        self._mcp_tracing_enabled = mcp_tracing_enabled if mcp_tracing_enabled is not None else cfg.mcp_tracing_enabled
        self._batch_size = batch_size or cfg.pipeline_batch_size
        flush_ms = flush_interval_ms or cfg.pipeline_flush_interval_ms
        self._flush_interval_s = flush_ms / 1000.0
        self._queue: asyncio.Queue[LLMEvent] = asyncio.Queue(maxsize=10_000)
        self._writer: EventWriter | None = None
        self._writer_task: asyncio.Task | None = None
        self._init_lock: asyncio.Lock = asyncio.Lock()

        # MCP tracing queues and writer (lazily started alongside EventWriter)
        self._mcp_call_queue: asyncio.Queue[MCPCall] = asyncio.Queue(maxsize=10_000)
        self._mcp_edge_queue: asyncio.Queue[MCPExecutionEdge] = asyncio.Queue(maxsize=10_000)
        self._mcp_writer: MCPWriter | None = None
        self._mcp_writer_task: asyncio.Task | None = None

    async def _ensure_writer(self) -> None:
        """Lazily start the background writer(s) on first event."""
        async with self._init_lock:
            if self._writer_task is None:
                self._writer = EventWriter(
                    db_url=self._db_url,
                    queue=self._queue,
                    batch_size=self._batch_size,
                    flush_interval_s=self._flush_interval_s,
                )
                self._writer_task = asyncio.create_task(self._writer.run())

            if self._mcp_tracing_enabled and self._mcp_writer_task is None:
                self._mcp_writer = MCPWriter(
                    db_url=self._db_url,
                    call_queue=self._mcp_call_queue,
                    edge_queue=self._mcp_edge_queue,
                    batch_size=self._batch_size,
                    flush_interval_s=self._flush_interval_s,
                )
                self._mcp_writer_task = asyncio.create_task(self._mcp_writer.run())

    async def close(self, timeout: float = 10.0) -> None:
        """Shut down the writer(s), draining queued events before exit."""
        if self._writer is not None:
            await self._writer.shutdown()
        if self._mcp_writer is not None:
            await self._mcp_writer.shutdown()

        if self._writer_task is not None:
            try:
                await asyncio.wait_for(self._writer_task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Writer task did not finish in %.1fs, cancelling", timeout)
                self._writer_task.cancel()
                try:
                    await self._writer_task
                except asyncio.CancelledError:
                    pass
            self._writer_task = None
            self._writer = None

        if self._mcp_writer_task is not None:
            try:
                await asyncio.wait_for(self._mcp_writer_task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("MCP writer task did not finish in %.1fs, cancelling", timeout)
                self._mcp_writer_task.cancel()
                try:
                    await self._mcp_writer_task
                except asyncio.CancelledError:
                    pass
            self._mcp_writer_task = None
            self._mcp_writer = None

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

        # MCP extraction: parse tool_use blocks for MCP server calls
        if self._mcp_tracing_enabled and event.has_tool_calls:
            self._extract_and_enqueue_mcp(response_obj, event)

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

    def _extract_and_enqueue_mcp(self, response_obj: Any, event: LLMEvent) -> None:
        """Parse MCP calls from the response and enqueue them for the MCP writer."""
        mcp_calls: list[MCPCall] = []

        choices = getattr(response_obj, "choices", [])
        message = getattr(choices[0], "message", None) if choices else None
        if not message:
            return

        # Anthropic-style: tool_use in content blocks
        content_blocks = getattr(message, "content", None)
        if isinstance(content_blocks, list):
            mcp_calls.extend(
                extract_mcp_calls(
                    content_blocks,
                    event_id=event.id,
                    trace_id=event.trace_id,
                    created_at=event.created_at,
                )
            )

        # OpenAI-style: tool_calls on the message object
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        if raw_tool_calls:
            mcp_calls.extend(
                extract_mcp_calls_from_tool_calls(
                    raw_tool_calls,
                    event_id=event.id,
                    trace_id=event.trace_id,
                    created_at=event.created_at,
                )
            )

        if not mcp_calls:
            return

        # Enqueue calls
        for call in mcp_calls:
            self._enqueue_mcp_call(call)

        # Build and enqueue DAG edges
        edges = build_execution_graph(mcp_calls)
        for edge in edges:
            self._enqueue_mcp_edge(edge)

    def _enqueue_mcp_call(self, call: MCPCall) -> None:
        """Non-blocking enqueue for MCP calls. Drops if buffer is full."""
        try:
            self._mcp_call_queue.put_nowait(call)
        except asyncio.QueueFull:
            logger.warning("MCP call queue full — dropping call %s", call.id)

    def _enqueue_mcp_edge(self, edge: MCPExecutionEdge) -> None:
        """Non-blocking enqueue for MCP edges. Drops if buffer is full."""
        try:
            self._mcp_edge_queue.put_nowait(edge)
        except asyncio.QueueFull:
            logger.warning("MCP edge queue full — dropping edge")

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
        cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

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
        user_kw_set: set[str] = set()
        has_code_fence = False
        has_json_schema = False
        output_format_hint: str | None = None

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

        # Trace context
        trace_ctx = extract_trace_context(kwargs)

        # Agent framework detection
        framework, agent_name = detect_agent_framework(kwargs)

        # Classification
        task_type = None
        task_type_confidence = None
        if self._enable_classification:
            token_ratio = compute_token_ratio(prompt_tokens, completion_tokens)
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
                user_prompt_keywords=list(user_kw_set),
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
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
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


# Backward-compat alias
AgentProofCallback = BlockThroughCallback
