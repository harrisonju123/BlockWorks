"""Extract MCP tool invocations from LLM responses.

Parses tool_use content blocks from completions to identify MCP server
calls, hashes parameters/responses, and builds the execution DAG for
parent-child relationships within a trace.

MCP tool names follow the convention: `{server_name}__{method_name}`
(double-underscore separator). Regular tool calls (without the separator)
are ignored.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from agentproof.mcp.types import MCPCall, MCPExecutionEdge
from agentproof.pipeline.hasher import hash_content
from agentproof.utils import utcnow

logger = logging.getLogger(__name__)

# MCP servers use double-underscore to namespace their tools
_MCP_SEPARATOR = "__"


def parse_mcp_tool_name(tool_name: str) -> tuple[str, str] | None:
    """Split an MCP-namespaced tool name into (server, method).

    Returns None if the name doesn't follow MCP convention.
    """
    if _MCP_SEPARATOR not in tool_name:
        return None

    parts = tool_name.split(_MCP_SEPARATOR, maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None

    return parts[0], parts[1]


def extract_mcp_calls(
    content_blocks: list[Any],
    event_id: uuid.UUID,
    trace_id: str,
    created_at: datetime | None = None,
) -> list[MCPCall]:
    """Extract MCP calls from tool_use content blocks in an LLM response.

    Each content block with type='tool_use' whose name matches the MCP
    naming convention ({server}__{method}) is converted to an MCPCall.
    """
    if created_at is None:
        created_at = utcnow()

    mcp_calls: list[MCPCall] = []

    for block in content_blocks:
        block_type = _get_attr_or_key(block, "type")
        if block_type != "tool_use":
            continue

        name = _get_attr_or_key(block, "name")
        if not name:
            continue

        parsed = parse_mcp_tool_name(name)
        if parsed is None:
            continue

        server_name, method = parsed

        # Hash the input parameters
        raw_input = _get_attr_or_key(block, "input") or {}
        params_hash = hash_content(json.dumps(raw_input, sort_keys=True))

        mcp_calls.append(
            MCPCall(
                id=uuid.uuid4(),
                event_id=event_id,
                trace_id=trace_id,
                created_at=created_at,
                server_name=server_name,
                method=method,
                params_hash=params_hash,
            )
        )

    return mcp_calls


def extract_mcp_calls_from_tool_calls(
    tool_calls: list[Any],
    event_id: uuid.UUID,
    trace_id: str,
    created_at: datetime | None = None,
) -> list[MCPCall]:
    """Extract MCP calls from OpenAI-style tool_calls on the message object.

    These have a .function.name and .function.arguments structure, distinct
    from the Anthropic content-block format.
    """
    if created_at is None:
        created_at = utcnow()

    mcp_calls: list[MCPCall] = []

    for tc in tool_calls:
        func = getattr(tc, "function", None)
        if func is None:
            continue

        name = getattr(func, "name", None)
        if not name:
            continue

        parsed = parse_mcp_tool_name(name)
        if parsed is None:
            continue

        server_name, method = parsed
        args_raw = getattr(func, "arguments", "") or ""
        params_hash = hash_content(args_raw)

        mcp_calls.append(
            MCPCall(
                id=uuid.uuid4(),
                event_id=event_id,
                trace_id=trace_id,
                created_at=created_at,
                server_name=server_name,
                method=method,
                params_hash=params_hash,
            )
        )

    return mcp_calls


def build_execution_graph(
    mcp_calls: list[MCPCall],
) -> list[MCPExecutionEdge]:
    """Build a sequential execution DAG from an ordered list of MCP calls.

    Within a single LLM response, tool_use blocks are ordered. Each call
    is treated as depending on the previous one (its output may inform
    the next call's parameters). For true causal DAG construction across
    multiple LLM turns, the caller should accumulate calls per trace and
    link the last call of turn N to the first call of turn N+1.
    """
    if len(mcp_calls) < 2:
        return []

    edges: list[MCPExecutionEdge] = []
    trace_id = mcp_calls[0].trace_id

    for i in range(len(mcp_calls) - 1):
        parent = mcp_calls[i]
        child = mcp_calls[i + 1]

        # Only link calls within the same trace
        if parent.trace_id != child.trace_id:
            continue

        edges.append(
            MCPExecutionEdge(
                id=uuid.uuid4(),
                parent_call_id=parent.id,
                child_call_id=child.id,
                trace_id=trace_id,
            )
        )

    return edges


def _get_attr_or_key(obj: Any, key: str) -> Any:
    """Access a value as either an attribute or dict key.

    LLM response content blocks may be dataclass-like objects or plain dicts
    depending on the provider SDK version.
    """
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
