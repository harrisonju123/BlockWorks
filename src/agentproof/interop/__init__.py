"""Cross-platform agent interoperability protocol.

Enables agents on different frameworks (LangChain, CrewAI, AutoGen,
custom) to discover and invoke each other through the registry with
payments flowing through state channels. The "HTTP for AI agents" layer.
"""

from agentproof.interop.bridge import DiscoveryBridge
from agentproof.interop.metering import MeteringStore
from agentproof.interop.protocol import (
    create_invocation,
    create_response_message,
    parse_response,
    validate_message,
    verify_message_signature,
)
from agentproof.interop.types import (
    AgentCapability,
    DisputeRecord,
    DisputeStatus,
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
    MeteringRecord,
    MessageType,
    ProtocolMessage,
)

__all__ = [
    "AgentCapability",
    "create_invocation",
    "create_response_message",
    "DiscoveryBridge",
    "DisputeRecord",
    "DisputeStatus",
    "InvocationRequest",
    "InvocationResponse",
    "InvocationStatus",
    "MeteringRecord",
    "MeteringStore",
    "MessageType",
    "parse_response",
    "ProtocolMessage",
    "validate_message",
    "verify_message_signature",
]
