"""Anthropic ↔ OpenAI message format conversion.

When the proxy receives an Anthropic-format /v1/messages request but needs to
forward it to an OpenAI model (via LiteLLM's /v1/chat/completions), we must
convert the request and response ourselves rather than relying on LiteLLM's
broken Anthropic→OpenAI translator (which mangles tool_use/tool_result pairing).

This module handles that bidirectional conversion.
"""

from __future__ import annotations

import json
from typing import Any


def anthropic_to_openai_request(body: dict) -> dict:
    """Convert an Anthropic /v1/messages request body to OpenAI /v1/chat/completions format.

    Handles: system, messages (text, tool_use, tool_result, image), tools, and
    common parameters (model, temperature, max_tokens, stream, stop, top_p).
    """
    openai_body: dict[str, Any] = {}

    # Model
    openai_body["model"] = body.get("model", "unknown")

    # Build messages
    openai_messages: list[dict] = []

    # System prompt → system message
    system = body.get("system", "")
    if isinstance(system, list):
        system = " ".join(
            b.get("text", "") for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        )
    if system:
        openai_messages.append({"role": "system", "content": system})

    # Convert each Anthropic message
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "assistant":
            openai_messages.extend(_convert_assistant_message(content))
        elif role == "user":
            openai_messages.extend(_convert_user_message(content))
        else:
            # Pass through any other roles as-is
            openai_messages.append(msg)

    openai_body["messages"] = openai_messages

    # Tools
    anthropic_tools = body.get("tools")
    if anthropic_tools:
        openai_body["tools"] = [_convert_tool_def(t) for t in anthropic_tools]

    # Common params
    for key in ("temperature", "top_p", "stream", "stop"):
        if key in body:
            openai_body[key] = body[key]

    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        openai_body["max_tokens"] = max_tokens

    # Stream options for token usage in final chunk
    if body.get("stream"):
        openai_body.setdefault("stream_options", {})["include_usage"] = True

    return openai_body


def openai_to_anthropic_response(data: dict, *, model: str | None = None) -> dict:
    """Convert an OpenAI /v1/chat/completions response to Anthropic /v1/messages format."""
    content_blocks: list[dict] = []
    stop_reason = "end_turn"

    for choice in data.get("choices", []):
        message = choice.get("message", {})

        # Text content
        text = message.get("content")
        if text:
            content_blocks.append({"type": "text", "text": text})

        # Tool calls
        for tc in message.get("tool_calls", []):
            fn = tc.get("function", {})
            try:
                tool_input = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                tool_input = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": tool_input,
            })

        finish = choice.get("finish_reason")
        if finish == "tool_calls":
            stop_reason = "tool_use"
        elif finish == "length":
            stop_reason = "max_tokens"
        elif finish == "stop":
            stop_reason = "end_turn"

    usage = data.get("usage", {})

    return {
        "id": data.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": model or data.get("model", "unknown"),
        "content": content_blocks or [{"type": "text", "text": ""}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_assistant_message(content: Any) -> list[dict]:
    """Convert an Anthropic assistant message to OpenAI format.

    Returns a list because one Anthropic message may produce one OpenAI message.
    """
    if isinstance(content, str):
        return [{"role": "assistant", "content": content}]

    if not isinstance(content, list):
        return [{"role": "assistant", "content": str(content) if content else ""}]

    text_parts: list[str] = []
    tool_calls: list[dict] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })
        elif btype == "thinking":
            # Skip thinking blocks — OpenAI doesn't support them
            pass

    msg: dict[str, Any] = {"role": "assistant"}
    if text_parts:
        msg["content"] = "\n".join(text_parts)
    else:
        msg["content"] = None
    if tool_calls:
        msg["tool_calls"] = tool_calls

    return [msg]


def _convert_user_message(content: Any) -> list[dict]:
    """Convert an Anthropic user message to OpenAI format.

    tool_result blocks become separate tool-role messages.
    Text blocks become a user message.
    """
    if isinstance(content, str):
        return [{"role": "user", "content": content}]

    if not isinstance(content, list):
        return [{"role": "user", "content": str(content) if content else ""}]

    tool_messages: list[dict] = []
    text_parts: list[str] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_result":
            # Convert tool_result to OpenAI tool response
            tr_content = block.get("content", "")
            if isinstance(tr_content, list):
                # Content can be a list of blocks
                tr_content = " ".join(
                    b.get("text", "") for b in tr_content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            tool_messages.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": str(tr_content),
            })
        elif btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "image":
            # Skip images for now — OpenAI format differs
            pass

    result: list[dict] = []
    # Tool responses must come before the user text
    result.extend(tool_messages)
    if text_parts:
        result.append({"role": "user", "content": "\n".join(text_parts)})
    elif not tool_messages:
        # No content at all — still need a user message
        result.append({"role": "user", "content": ""})

    return result


def _convert_tool_def(tool: dict) -> dict:
    """Convert an Anthropic tool definition to OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }
