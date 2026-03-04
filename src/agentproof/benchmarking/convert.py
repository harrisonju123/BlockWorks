"""Anthropic → OpenAI message format converter for benchmark replay.

LiteLLM's acompletion() only accepts OpenAI-format messages. Anthropic uses
content-block arrays with tool_use/tool_result blocks, plus a separate `system`
field. This module converts between formats so the benchmark pipeline can
replay Anthropic-native traffic through litellm.

Pure functions, no I/O.
"""

from __future__ import annotations

import json
from typing import Any


def is_anthropic_format(messages: list[dict]) -> bool:
    """Detect whether messages use Anthropic-native content-block arrays.

    Returns True if any message has a `content` field that is a list of dicts
    with a `type` key (Anthropic's content block format). Plain string content
    or OpenAI-format messages return False.
    """
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "type" in block:
                    return True
    return False


def convert_anthropic_to_openai(
    messages: list[dict],
    system: str | list | None = None,
) -> list[dict]:
    """Convert Anthropic-format messages to OpenAI-format for litellm replay.

    Handles:
    - system (string or content-block list) → role: "system" message
    - Text content blocks → plain string
    - tool_use blocks in assistant → OpenAI tool_calls array
    - tool_result blocks in user → separate role: "tool" messages
    - Thinking/image blocks → stripped (not needed for replay)
    - Plain string content → passthrough
    """
    result: list[dict] = []

    # Prepend system prompt if provided
    if system:
        if isinstance(system, list):
            text = " ".join(
                b.get("text", "") for b in system
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            text = system
        if text:
            result.append({"role": "system", "content": text})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        # Plain string content — passthrough
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        # Not a list — skip empty/None
        if not isinstance(content, list):
            result.append({"role": role, "content": content or ""})
            continue

        if role == "assistant":
            result.extend(_convert_assistant_message(content))
        elif role == "user":
            result.extend(_convert_user_message(content))
        else:
            # Other roles (shouldn't happen, but be safe)
            text = _extract_text(content)
            result.append({"role": role, "content": text})

    return result


def _convert_assistant_message(content: list[dict]) -> list[dict]:
    """Convert an Anthropic assistant message with content blocks."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })
        # thinking, image, etc. — stripped

    msg: dict[str, Any] = {"role": "assistant"}
    if text_parts:
        msg["content"] = "".join(text_parts)
    else:
        msg["content"] = None
    if tool_calls:
        msg["tool_calls"] = tool_calls

    return [msg]


def _convert_user_message(content: list[dict]) -> list[dict]:
    """Convert an Anthropic user message, splitting tool_result blocks out."""
    text_parts: list[str] = []
    tool_messages: list[dict] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_result":
            # Extract text from nested content blocks if present
            tr_content = block.get("content")
            if isinstance(tr_content, list):
                tr_text = " ".join(
                    b.get("text", "") for b in tr_content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            elif isinstance(tr_content, str):
                tr_text = tr_content
            else:
                tr_text = ""
            tool_messages.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": tr_text,
            })
        # image, thinking — stripped

    result: list[dict] = []
    if text_parts:
        result.append({"role": "user", "content": "".join(text_parts)})
    if tool_messages:
        result.extend(tool_messages)

    # If nothing was produced (e.g. only images), emit empty user message
    if not result:
        result.append({"role": "user", "content": ""})

    return result


def _extract_text(content: list[dict]) -> str:
    """Pull text from content blocks, ignoring non-text types."""
    return "".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )
