"""Shared utility functions used across the agentproof codebase."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)
