"""Tests for interop type models — serialization, defaults, enums."""

from __future__ import annotations

from datetime import datetime, timezone

from blockthrough.interop.types import (
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


class TestInvocationRequest:

    def test_defaults(self) -> None:
        req = InvocationRequest(
            caller_agent_id="agent-a",
            target_listing_id="listing-b",
            method="search",
        )
        assert req.params == {}
        assert req.max_cost == 1.0
        assert req.timeout_s == 30
        assert req.trace_id == ""

    def test_with_params(self) -> None:
        req = InvocationRequest(
            caller_agent_id="agent-a",
            target_listing_id="listing-b",
            method="generate",
            params={"prompt": "hello"},
            max_cost=0.5,
        )
        assert req.params["prompt"] == "hello"
        assert req.max_cost == 0.5


class TestInvocationResponse:

    def test_success(self) -> None:
        resp = InvocationResponse(
            request_id="req-1",
            status=InvocationStatus.SUCCESS,
            result={"output": "done"},
            cost=0.01,
            latency_ms=42.5,
            target_framework="langchain",
        )
        assert resp.status == InvocationStatus.SUCCESS
        assert resp.target_framework == "langchain"

    def test_failure(self) -> None:
        resp = InvocationResponse(
            request_id="req-2",
            status=InvocationStatus.FAILURE,
        )
        assert resp.result == {}
        assert resp.cost == 0.0

    def test_timeout(self) -> None:
        resp = InvocationResponse(
            request_id="req-3",
            status=InvocationStatus.TIMEOUT,
        )
        assert resp.status == InvocationStatus.TIMEOUT


class TestAgentCapability:

    def test_defaults(self) -> None:
        cap = AgentCapability(listing_id="listing-1")
        assert cap.methods == []
        assert cap.input_schema == {}
        assert cap.output_schema == {}
        assert cap.supported_frameworks == []

    def test_with_methods(self) -> None:
        cap = AgentCapability(
            listing_id="listing-1",
            methods=["search", "generate"],
            supported_frameworks=["langchain", "crewai"],
        )
        assert len(cap.methods) == 2
        assert "langchain" in cap.supported_frameworks


class TestProtocolMessage:

    def test_invoke_message(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.INVOKE,
            sender="agent-a",
            receiver="listing-b",
            payload={"request_id": "r1", "method": "search"},
            timestamp=now,
        )
        assert msg.version == "1.0"
        assert msg.type == MessageType.INVOKE
        assert msg.signature == ""

    def test_response_message(self) -> None:
        now = datetime.now(timezone.utc)
        msg = ProtocolMessage(
            type=MessageType.RESPONSE,
            sender="listing-b",
            receiver="agent-a",
            payload={"request_id": "r1", "status": "success"},
            timestamp=now,
        )
        assert msg.type == MessageType.RESPONSE


class TestDisputeRecord:

    def test_open_dispute(self) -> None:
        now = datetime.now(timezone.utc)
        dispute = DisputeRecord(
            id="d-1",
            invocation_id="inv-1",
            initiator="agent-a",
            reason="Incorrect response",
            evidence_hash="abc123",
            opened_at=now,
        )
        assert dispute.status == DisputeStatus.OPEN
        assert dispute.resolution == ""
        assert dispute.resolved_at is None

    def test_resolved_dispute(self) -> None:
        now = datetime.now(timezone.utc)
        dispute = DisputeRecord(
            id="d-2",
            invocation_id="inv-2",
            initiator="agent-b",
            reason="Timeout",
            evidence_hash="def456",
            status=DisputeStatus.RESOLVED,
            resolution="Refund issued",
            resolver="arbitrator",
            opened_at=now,
            resolved_at=now,
        )
        assert dispute.status == DisputeStatus.RESOLVED
        assert dispute.resolution == "Refund issued"


class TestMeteringRecord:

    def test_defaults(self) -> None:
        now = datetime.now(timezone.utc)
        record = MeteringRecord(
            invocation_id="inv-1",
            caller_id="agent-a",
            target_id="listing-b",
            timestamp=now,
        )
        assert record.tokens_used == 0
        assert record.cost == 0.0
        assert record.latency_ms == 0.0
