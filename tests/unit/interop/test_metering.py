"""Tests for metering and dispute resolution."""

from __future__ import annotations

import pytest

from blockthrough.interop.metering import (
    DisputeAlreadyResolvedError,
    DisputeNotFoundError,
    MeteringStore,
)
from blockthrough.interop.types import (
    DisputeStatus,
    InvocationRequest,
    InvocationResponse,
    InvocationStatus,
)


def _make_request(**overrides) -> InvocationRequest:
    defaults = dict(
        caller_agent_id="agent-a",
        target_listing_id="listing-b",
        method="search",
        params={"query": "test data for tokens"},
    )
    defaults.update(overrides)
    return InvocationRequest(**defaults)


def _make_response(**overrides) -> InvocationResponse:
    defaults = dict(
        request_id="req-001",
        status=InvocationStatus.SUCCESS,
        result={"answer": "the result is forty-two"},
        cost=0.01,
        latency_ms=15.0,
        target_framework="langchain",
    )
    defaults.update(overrides)
    return InvocationResponse(**defaults)


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------


class TestMeterInvocation:

    def test_creates_record(self) -> None:
        store = MeteringStore()
        request = _make_request()
        response = _make_response()

        record = store.meter_invocation(request, response)

        assert record.invocation_id == "req-001"
        assert record.caller_id == "agent-a"
        assert record.target_id == "listing-b"
        assert record.cost == 0.01
        assert record.latency_ms == 15.0

    def test_estimates_tokens(self) -> None:
        store = MeteringStore()
        request = _make_request(params={"text": "a" * 100})
        response = _make_response(result={"data": "b" * 200})

        record = store.meter_invocation(request, response)

        # Token estimate is based on combined char length / 4
        assert record.tokens_used > 0

    def test_minimum_one_token(self) -> None:
        store = MeteringStore()
        request = _make_request(params={})
        response = _make_response(result={})

        record = store.meter_invocation(request, response)
        assert record.tokens_used >= 1

    def test_get_record(self) -> None:
        store = MeteringStore()
        request = _make_request()
        response = _make_response()
        store.meter_invocation(request, response)

        record = store.get_record("req-001")
        assert record is not None
        assert record.caller_id == "agent-a"

    def test_get_record_missing(self) -> None:
        store = MeteringStore()
        assert store.get_record("nonexistent") is None

    def test_get_records_for_caller(self) -> None:
        store = MeteringStore()
        store.meter_invocation(_make_request(), _make_response(request_id="r1"))
        store.meter_invocation(
            _make_request(caller_agent_id="agent-c"),
            _make_response(request_id="r2"),
        )

        records = store.get_records_for("agent-a")
        assert len(records) == 1
        assert records[0].caller_id == "agent-a"

    def test_get_records_for_target(self) -> None:
        store = MeteringStore()
        store.meter_invocation(_make_request(), _make_response(request_id="r1"))

        records = store.get_records_for("listing-b")
        assert len(records) == 1
        assert records[0].target_id == "listing-b"


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------


class TestOpenDispute:

    def test_creates_dispute(self) -> None:
        store = MeteringStore()
        dispute = store.open_dispute(
            invocation_id="inv-1",
            initiator="agent-a",
            reason="Wrong response",
            evidence_hash="abc123",
        )

        assert dispute.id  # non-empty UUID
        assert dispute.invocation_id == "inv-1"
        assert dispute.initiator == "agent-a"
        assert dispute.reason == "Wrong response"
        assert dispute.evidence_hash == "abc123"
        assert dispute.status == DisputeStatus.OPEN
        assert dispute.resolved_at is None

    def test_unique_dispute_ids(self) -> None:
        store = MeteringStore()
        d1 = store.open_dispute("inv-1", "a", "reason1", "hash1")
        d2 = store.open_dispute("inv-1", "a", "reason2", "hash2")
        assert d1.id != d2.id


class TestResolveDispute:

    def test_resolves_open_dispute(self) -> None:
        store = MeteringStore()
        dispute = store.open_dispute("inv-1", "agent-a", "Bad result", "hash1")

        resolved = store.resolve_dispute(
            dispute_id=dispute.id,
            resolution="Refund granted",
            resolver="arbitrator",
        )

        assert resolved.status == DisputeStatus.RESOLVED
        assert resolved.resolution == "Refund granted"
        assert resolved.resolver == "arbitrator"
        assert resolved.resolved_at is not None

    def test_resolve_not_found_raises(self) -> None:
        store = MeteringStore()
        with pytest.raises(DisputeNotFoundError):
            store.resolve_dispute("nonexistent", "resolution", "resolver")

    def test_resolve_already_resolved_raises(self) -> None:
        store = MeteringStore()
        dispute = store.open_dispute("inv-1", "agent-a", "Bad result", "hash1")
        store.resolve_dispute(dispute.id, "Fixed", "arbitrator")

        with pytest.raises(DisputeAlreadyResolvedError):
            store.resolve_dispute(dispute.id, "Fixed again", "arbitrator")


class TestGetDispute:

    def test_get_existing_dispute(self) -> None:
        store = MeteringStore()
        dispute = store.open_dispute("inv-1", "agent-a", "Reason", "hash")

        fetched = store.get_dispute(dispute.id)
        assert fetched.id == dispute.id

    def test_get_not_found_raises(self) -> None:
        store = MeteringStore()
        with pytest.raises(DisputeNotFoundError):
            store.get_dispute("ghost")


class TestGetDisputesFor:

    def test_returns_disputes_for_invocation(self) -> None:
        store = MeteringStore()
        store.open_dispute("inv-1", "agent-a", "Reason 1", "hash1")
        store.open_dispute("inv-1", "agent-b", "Reason 2", "hash2")
        store.open_dispute("inv-2", "agent-a", "Other", "hash3")

        disputes = store.get_disputes_for("inv-1")
        assert len(disputes) == 2
        assert all(d.invocation_id == "inv-1" for d in disputes)

    def test_returns_empty_for_unknown(self) -> None:
        store = MeteringStore()
        assert store.get_disputes_for("unknown") == []


class TestMeteringStoreReset:

    def test_clears_all(self) -> None:
        store = MeteringStore()
        store.meter_invocation(_make_request(), _make_response())
        store.open_dispute("inv-1", "a", "r", "h")

        store.reset()

        assert store.get_record("req-001") is None
        assert store.get_disputes_for("inv-1") == []
