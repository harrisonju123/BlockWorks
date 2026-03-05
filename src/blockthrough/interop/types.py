"""Pydantic models for cross-platform agent interoperability.

Defines the wire format, invocation lifecycle types, and capability
descriptors that enable agents on different frameworks to discover
and invoke each other through a standard protocol.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class InvocationStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"


class DisputeStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class MessageType(str, enum.Enum):
    """Protocol message types for the interop wire format."""

    INVOKE = "invoke"
    RESPONSE = "response"
    CAPABILITY_QUERY = "capability_query"
    CAPABILITY_RESPONSE = "capability_response"


class InvocationRequest(BaseModel):
    """Request to invoke an agent or MCP server through the protocol."""

    caller_agent_id: str
    target_listing_id: str
    method: str
    params: dict = Field(default_factory=dict)
    max_cost: float = 1.0
    timeout_s: int = 30
    trace_id: str = ""


class InvocationResponse(BaseModel):
    """Response from a cross-framework invocation."""

    request_id: str
    status: InvocationStatus
    result: dict = Field(default_factory=dict)
    cost: float = 0.0
    latency_ms: float = 0.0
    target_framework: str = ""


class AgentCapability(BaseModel):
    """Describes what an agent can do and how to call it."""

    listing_id: str
    methods: list[str] = Field(default_factory=list)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    supported_frameworks: list[str] = Field(default_factory=list)


class ProtocolMessage(BaseModel):
    """Standard wire format for cross-framework communication.

    JSON-based envelope carrying invocation requests and responses
    between agents on different frameworks. Signed with HMAC to
    prevent tampering on the wire.
    """

    version: str = "1.0"
    type: MessageType
    sender: str
    receiver: str
    payload: dict = Field(default_factory=dict)
    signature: str = ""
    timestamp: datetime


class MeteringRecord(BaseModel):
    """Tracks resource usage for a single invocation."""

    invocation_id: str
    caller_id: str
    target_id: str
    tokens_used: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    timestamp: datetime


class DisputeRecord(BaseModel):
    """Tracks a dispute over an invocation outcome."""

    id: str
    invocation_id: str
    initiator: str
    reason: str
    evidence_hash: str
    status: DisputeStatus = DisputeStatus.OPEN
    resolution: str = ""
    resolver: str = ""
    opened_at: datetime
    resolved_at: datetime | None = None
