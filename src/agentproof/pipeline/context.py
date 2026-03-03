"""Trace context propagation and agent framework detection.

Extracts session IDs, trace/span IDs, and attempts to identify
which agent framework initiated the LLM call.
"""

import uuid
from typing import Any


_FRAMEWORK_HINTS = {
    "langchain": ["langchain", "langsmith"],
    "crewai": ["crewai"],
    "claude-code": ["claude-code", "claude_code"],
    "autogen": ["autogen"],
    "opencode": ["opencode"],
}


def _litellm_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    return kwargs.get("litellm_params", {})


def _litellm_metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    return _litellm_params(kwargs).get("metadata", {})


def extract_trace_context(kwargs: dict[str, Any]) -> dict[str, str | None]:
    """Pull trace/span/session IDs from LiteLLM callback kwargs."""
    metadata = _litellm_metadata(kwargs)
    fallback_id = kwargs.get("litellm_call_id") or uuid.uuid4().hex

    return {
        "trace_id": metadata.get("trace_id") or fallback_id,
        "span_id": fallback_id,
        "session_id": metadata.get("session_id"),
        "parent_span_id": metadata.get("parent_span_id"),
    }


def detect_agent_framework(kwargs: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best-effort detection of which agent framework initiated this call.

    Returns (framework_name, agent_name) tuple.
    """
    metadata = _litellm_metadata(kwargs)

    # Explicit metadata takes priority
    if "agent_framework" in metadata:
        return metadata["agent_framework"], metadata.get("agent_name")

    # Check user-agent or other header hints
    headers = _litellm_params(kwargs).get("headers", {})
    user_agent = headers.get("User-Agent", "").lower()

    for framework, hints in _FRAMEWORK_HINTS.items():
        if any(hint in user_agent for hint in hints):
            return framework, metadata.get("agent_name")

    # Check model group naming conventions
    model_group = kwargs.get("model_group", "")
    if model_group:
        model_group_lower = model_group.lower()
        for framework, hints in _FRAMEWORK_HINTS.items():
            if any(hint in model_group_lower for hint in hints):
                return framework, metadata.get("agent_name")

    return None, None
