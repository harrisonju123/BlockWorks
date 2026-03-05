"""Standard communication protocol for cross-framework agent invocation.

Handles message creation, parsing, validation, and HMAC signing.
The wire format is JSON with a version, type, sender/receiver,
payload, signature, and timestamp. Signing reuses the HMAC-SHA256
pattern from channels/signing.py — local dev placeholder for real
ECDSA that can be swapped without changing this module's interface.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from blockthrough.interop.types import (
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
    MessageType,
    ProtocolMessage,
)
from blockthrough.utils import utcnow

PROTOCOL_VERSION = "1.0"


class ProtocolError(Exception):
    """Raised when message validation or parsing fails."""


def _serialize_payload(payload: dict) -> str:
    """Canonical JSON serialization for signing — deterministic key order, no whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign_message(payload: dict, secret: str) -> str:
    """HMAC-SHA256 of the canonicalized payload.

    Local dev placeholder — swap in ECDSA without changing callers.
    """
    canonical = _serialize_payload(payload)
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_message_signature(message: ProtocolMessage, secret: str) -> bool:
    """Verify the HMAC signature on a protocol message."""
    expected = sign_message(message.payload, secret)
    return hmac.compare_digest(expected, message.signature)


def create_invocation(
    request: InvocationRequest,
    secret: str | None = None,
) -> ProtocolMessage:
    """Build a signed INVOKE protocol message from an invocation request."""
    if not secret:
        from blockthrough.config import get_config
        secret = get_config().interop_signing_secret or "dev-only-key"
    payload = {
        "request_id": str(uuid.uuid4()),
        "caller_agent_id": request.caller_agent_id,
        "target_listing_id": request.target_listing_id,
        "method": request.method,
        "params": request.params,
        "max_cost": request.max_cost,
        "timeout_s": request.timeout_s,
        "trace_id": request.trace_id,
    }

    signature = sign_message(payload, secret)

    return ProtocolMessage(
        version=PROTOCOL_VERSION,
        type=MessageType.INVOKE,
        sender=request.caller_agent_id,
        receiver=request.target_listing_id,
        payload=payload,
        signature=signature,
        timestamp=utcnow(),
    )


def create_response_message(
    response: InvocationResponse,
    receiver: str,
    secret: str | None = None,
) -> ProtocolMessage:
    """Build a signed RESPONSE protocol message from an invocation response."""
    if not secret:
        from blockthrough.config import get_config
        secret = get_config().interop_signing_secret or "dev-only-key"
    payload = {
        "request_id": response.request_id,
        "status": response.status.value,
        "result": response.result,
        "cost": response.cost,
        "latency_ms": response.latency_ms,
        "target_framework": response.target_framework,
    }

    signature = sign_message(payload, secret)

    return ProtocolMessage(
        version=PROTOCOL_VERSION,
        type=MessageType.RESPONSE,
        sender=response.request_id,
        receiver=receiver,
        payload=payload,
        signature=signature,
        timestamp=utcnow(),
    )


def parse_response(message: ProtocolMessage) -> InvocationResponse:
    """Extract an InvocationResponse from a RESPONSE protocol message.

    Raises:
        ProtocolError: If the message type is not RESPONSE or required
            fields are missing from the payload.
    """
    if message.type != MessageType.RESPONSE:
        raise ProtocolError(
            f"Expected RESPONSE message, got {message.type.value}"
        )

    payload = message.payload
    required = {"request_id", "status"}
    missing = required - set(payload.keys())
    if missing:
        raise ProtocolError(f"Missing required fields: {missing}")

    try:
        status = InvocationStatus(payload["status"])
    except ValueError:
        raise ProtocolError(f"Invalid status: {payload['status']}")

    return InvocationResponse(
        request_id=payload["request_id"],
        status=status,
        result=payload.get("result", {}),
        cost=payload.get("cost", 0.0),
        latency_ms=payload.get("latency_ms", 0.0),
        target_framework=payload.get("target_framework", ""),
    )


def validate_message(message: ProtocolMessage) -> bool:
    """Structural validation of a protocol message.

    Checks version, required fields, and message type validity.
    Does NOT verify the signature — call verify_message_signature
    separately when the secret is available.
    """
    if message.version != PROTOCOL_VERSION:
        return False

    if not message.sender or not message.receiver:
        return False

    if not message.payload:
        return False

    if message.type == MessageType.INVOKE:
        required = {"request_id", "caller_agent_id", "target_listing_id", "method"}
        if not required.issubset(set(message.payload.keys())):
            return False

    elif message.type == MessageType.RESPONSE:
        required = {"request_id", "status"}
        if not required.issubset(set(message.payload.keys())):
            return False

    return True
