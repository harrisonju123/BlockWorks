"""Shared utility functions used across the agentproof codebase."""

from datetime import datetime, timezone
from functools import lru_cache


def utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


@lru_cache(maxsize=64)
def infer_provider(model: str) -> str:
    """Infer the cloud provider from a model name. Cached for hot-path use."""
    name = model.lower()
    if "claude" in name:
        return "anthropic"
    if any(tag in name for tag in ("gpt", "o1", "o3")):
        return "openai"
    if "qwen" in name:
        return "alibaba"
    if "gemma" in name or name.startswith("google."):
        return "google"
    if "ministral" in name or "mistral" in name:
        return "mistral"
    if "kimi" in name or "moonshot" in name:
        return "moonshot"
    if "minimax" in name:
        return "minimax"
    if "nova" in name or name.startswith("us.amazon."):
        return "amazon"
    if name.startswith("openai."):
        return "openai"
    return "unknown"
