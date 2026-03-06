"""Core types shared across all AgentProof components.

The LLMEvent model is the single source of truth for the data pipeline.
Both the callback handler (writer) and the API layer (reader) import from here.
Changes to this module require review from pipeline and API owners.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EventStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class TaskType(str, enum.Enum):
    """Task taxonomy — v1 frozen for Phase 0.

    New types can be added. Existing types and their string
    representations must not change without a migration.
    """

    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"
    EXTRACTION = "extraction"
    REASONING = "reasoning"
    CONVERSATION = "conversation"
    TOOL_SELECTION = "tool_selection"
    ARCHITECTURE = "architecture"
    DEBUGGING = "debugging"
    REFACTORING = "refactoring"
    DOCUMENTATION = "documentation"
    TESTING = "testing"
    UNKNOWN = "unknown"


class ToolCallRecord(BaseModel):
    """A single tool/function call within an LLM completion."""

    tool_name: str
    args_hash: str
    response_summary_hash: str | None = None


class LLMEvent(BaseModel):
    """The core event written to TimescaleDB by the callback handler.

    Every field here maps 1:1 to a column in the llm_events table.
    Content is never stored raw — only SHA-256 fingerprints.
    """

    id: UUID
    created_at: datetime
    status: EventStatus

    # Provider and model
    provider: str
    model: str
    model_group: str | None = None

    # Token usage
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    # Cost (USD)
    estimated_cost: float
    custom_pricing: float | None = None

    # Latency
    latency_ms: float
    time_to_first_token_ms: float | None = None

    # Content hashes
    prompt_hash: str
    completion_hash: str
    system_prompt_hash: str | None = None

    # Trace context
    session_id: str | None = None
    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    # Agent framework detection
    agent_framework: str | None = None
    agent_name: str | None = None

    # Tool calls
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    has_tool_calls: bool = False

    # Classification (populated by classifier, may be null initially)
    task_type: TaskType | None = None
    task_type_confidence: float | None = None

    # Error context (for failures)
    error_type: str | None = None
    error_message_hash: str | None = None

    # Metadata
    litellm_call_id: str
    api_base: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    custom_metadata: dict | None = None
