"""Tests for the interop protocol — message creation, parsing, validation, signing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentproof.interop.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    create_invocation,
    create_response_message,
    parse_response,
    sign_message,
    validate_message,
    verify_message_signature,
)
from agentproof.interop.types import (
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
    MessageType,
    ProtocolMessage,
)


def _make_request(**overrides) -> InvocationRequest:
    defaults = dict(
        caller_agent_id="agent-a",
        target_listing_id="listing-b",
        method="search",
        params={"query": "test"},
        max_cost=0.5,
        timeout_s=10,
        trace_id="trace-001",
    )
    defaults.update(overrides)
    return InvocationRequest(**defaults)


def _make_response(**overrides) -> InvocationResponse:
    defaults = dict(
        request_id="req-123",
        status=InvocationStatus.SUCCESS,
        result={"answer": "42"},
        cost=0.01,
        latency_ms=15.0,
        target_framework="langchain",
    )
    defaults.update(overrides)
    return InvocationResponse(**defaults)


# ---------------------------------------------------------------------------
# Message creation
# ---------------------------------------------------------------------------


class TestCreateInvocation:

    def test_creates_invoke_message(self) -> None:
        request = _make_request()
        msg = create_invocation(request)

        assert msg.version == PROTOCOL_VERSION
        assert msg.type == MessageType.INVOKE
        assert msg.sender == "agent-a"
        assert msg.receiver == "listing-b"
        assert msg.payload["method"] == "search"
        assert msg.payload["request_id"]  # non-empty UUID
        assert msg.signature  # non-empty HMAC

    def test_payload_contains_all_fields(self) -> None:
        request = _make_request()
        msg = create_invocation(request)
        payload = msg.payload

        assert payload["caller_agent_id"] == "agent-a"
        assert payload["target_listing_id"] == "listing-b"
        assert payload["method"] == "search"
        assert payload["params"] == {"query": "test"}
        assert payload["max_cost"] == 0.5
        assert payload["timeout_s"] == 10
        assert payload["trace_id"] == "trace-001"

    def test_unique_request_ids(self) -> None:
        request = _make_request()
        msg1 = create_invocation(request)
        msg2 = create_invocation(request)
        assert msg1.payload["request_id"] != msg2.payload["request_id"]

    def test_custom_secret_changes_signature(self) -> None:
        request = _make_request()
        msg1 = create_invocation(request, secret="key-1")
        msg2 = create_invocation(request, secret="key-2")
        # Same payload structure but different secrets produce different sigs
        # (request_ids differ too, but signature algorithm is the differentiator)
        assert msg1.signature != msg2.signature


class TestCreateResponseMessage:

    def test_creates_response_message(self) -> None:
        response = _make_response()
        msg = create_response_message(response, receiver="agent-a")

        assert msg.type == MessageType.RESPONSE
        assert msg.payload["status"] == "success"
        assert msg.payload["request_id"] == "req-123"
        assert msg.signature

    def test_response_payload_fields(self) -> None:
        response = _make_response()
        msg = create_response_message(response, receiver="agent-a")
        payload = msg.payload

        assert payload["result"] == {"answer": "42"}
        assert payload["cost"] == 0.01
        assert payload["latency_ms"] == 15.0
        assert payload["target_framework"] == "langchain"


# ---------------------------------------------------------------------------
# Message signing / verification
# ---------------------------------------------------------------------------


class TestSigning:

    def test_sign_and_verify(self) -> None:
        request = _make_request()
        secret = "my-secret"
        msg = create_invocation(request, secret=secret)

        assert verify_message_signature(msg, secret)

    def test_wrong_secret_fails_verification(self) -> None:
        request = _make_request()
        msg = create_invocation(request, secret="correct-key")

        assert not verify_message_signature(msg, "wrong-key")

    def test_tampered_payload_fails_verification(self) -> None:
        request = _make_request()
        secret = "my-secret"
        msg = create_invocation(request, secret=secret)

        # Tamper with the payload
        msg.payload["method"] = "hacked"

        assert not verify_message_signature(msg, secret)

    def test_sign_message_deterministic(self) -> None:
        payload = {"a": 1, "b": 2}
        sig1 = sign_message(payload, "key")
        sig2 = sign_message(payload, "key")
        assert sig1 == sig2

    def test_sign_message_key_order_independent(self) -> None:
        """Canonical serialization means key order shouldn't matter."""
        payload_a = {"z": 1, "a": 2}
        payload_b = {"a": 2, "z": 1}
        assert sign_message(payload_a, "key") == sign_message(payload_b, "key")


# ---------------------------------------------------------------------------
# Parse response
# ---------------------------------------------------------------------------


class TestParseResponse:

    def test_parse_success_response(self) -> None:
        response = _make_response()
        msg = create_response_message(response, receiver="agent-a")

        parsed = parse_response(msg)
        assert parsed.request_id == "req-123"
        assert parsed.status == InvocationStatus.SUCCESS
        assert parsed.result == {"answer": "42"}
        assert parsed.cost == 0.01

    def test_parse_failure_response(self) -> None:
        response = _make_response(status=InvocationStatus.FAILURE, result={"error": "bad"})
        msg = create_response_message(response, receiver="agent-a")

        parsed = parse_response(msg)
        assert parsed.status == InvocationStatus.FAILURE
        assert parsed.result["error"] == "bad"

    def test_parse_rejects_non_response_message(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.INVOKE,
            sender="a",
            receiver="b",
            payload={"request_id": "r1"},
            timestamp=now,
        )
        with pytest.raises(ProtocolError, match="Expected RESPONSE"):
            parse_response(msg)

    def test_parse_rejects_missing_fields(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.RESPONSE,
            sender="b",
            receiver="a",
            payload={"some_field": "value"},  # missing request_id and status
            timestamp=now,
        )
        with pytest.raises(ProtocolError, match="Missing required"):
            parse_response(msg)

    def test_parse_rejects_invalid_status(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.RESPONSE,
            sender="b",
            receiver="a",
            payload={"request_id": "r1", "status": "exploded"},
            timestamp=now,
        )
        with pytest.raises(ProtocolError, match="Invalid status"):
            parse_response(msg)


# ---------------------------------------------------------------------------
# Validate message
# ---------------------------------------------------------------------------


class TestValidateMessage:

    def test_valid_invoke_message(self) -> None:
        request = _make_request()
        msg = create_invocation(request)
        assert validate_message(msg) is True

    def test_valid_response_message(self) -> None:
        response = _make_response()
        msg = create_response_message(response, receiver="agent-a")
        assert validate_message(msg) is True

    def test_wrong_version_fails(self) -> None:
        request = _make_request()
        msg = create_invocation(request)
        msg.version = "0.1"
        assert validate_message(msg) is False

    def test_empty_sender_fails(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.INVOKE,
            sender="",
            receiver="b",
            payload={"request_id": "r1", "caller_agent_id": "a", "target_listing_id": "b", "method": "x"},
            timestamp=now,
        )
        assert validate_message(msg) is False

    def test_empty_receiver_fails(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.INVOKE,
            sender="a",
            receiver="",
            payload={"request_id": "r1", "caller_agent_id": "a", "target_listing_id": "b", "method": "x"},
            timestamp=now,
        )
        assert validate_message(msg) is False

    def test_empty_payload_fails(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.INVOKE,
            sender="a",
            receiver="b",
            payload={},
            timestamp=now,
        )
        assert validate_message(msg) is False

    def test_invoke_missing_required_fields_fails(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.INVOKE,
            sender="a",
            receiver="b",
            payload={"request_id": "r1"},  # missing method, caller, target
            timestamp=now,
        )
        assert validate_message(msg) is False

    def test_response_missing_required_fields_fails(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.RESPONSE,
            sender="b",
            receiver="a",
            payload={"request_id": "r1"},  # missing status
            timestamp=now,
        )
        assert validate_message(msg) is False
