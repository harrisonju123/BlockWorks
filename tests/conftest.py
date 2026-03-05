"""Shared test fixtures."""

import uuid
from datetime import datetime, timezone

import pytest

from blockthrough.types import EventStatus, LLMEvent


@pytest.fixture
def sample_event() -> LLMEvent:
    """A minimal valid LLMEvent for testing."""
    return LLMEvent(
        id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        status=EventStatus.SUCCESS,
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        estimated_cost=0.0015,
        latency_ms=1234.5,
        prompt_hash="abc123",
        completion_hash="def456",
        trace_id="trace-001",
        span_id="span-001",
        litellm_call_id="call-001",
    )
