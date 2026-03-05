"""Tests for the interop API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from blockthrough.api.app import app
from blockthrough.api.routes.interop import (
    _get_bridge,
    _get_metering,
    _get_registry,
    reset_stores,
)
from blockthrough.config import get_config
from blockthrough.registry.types import (
    AgentListing,
    ListingCategory,
    MCPServerListing,
    PricingModel,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset singletons and config between tests."""
    reset_stores()
    get_config.cache_clear()
    yield
    reset_stores()
    get_config.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def _seed_listing(endpoint_url: str = "http://localhost:9000/invoke", **kwargs) -> AgentListing:
    """Register a listing and return it for use in tests."""
    now = datetime.now(timezone.utc)
    registry = _get_registry()

    defaults = dict(
        id="",
        name="Test Agent",
        description="A test agent",
        owner_address="0xowner1",
        category=ListingCategory.AGENT,
        pricing_model=PricingModel.FREE,
        stake_amount=0.05,
        endpoint_url=endpoint_url,
        registered_at=now,
        last_active=now,
    )
    defaults.update(kwargs)
    listing = AgentListing(**defaults)
    return registry.register_listing(listing)


def _seed_mcp_listing(**kwargs) -> MCPServerListing:
    now = datetime.now(timezone.utc)
    registry = _get_registry()

    defaults = dict(
        id="",
        name="Test MCP",
        description="A test MCP server",
        owner_address="0xmcp_owner",
        category=ListingCategory.MCP_SERVER,
        stake_amount=0.05,
        supported_methods=["search", "execute"],
        endpoint_url="http://localhost:9001/mcp",
        registered_at=now,
        last_active=now,
    )
    defaults.update(kwargs)
    listing = MCPServerListing(**defaults)
    return registry.register_listing(listing)


def _enable_interop(monkeypatch):
    """Enable the interop feature flag."""
    monkeypatch.setenv("AGENTPROOF_INTEROP_ENABLED", "true")
    get_config.cache_clear()


# ---------------------------------------------------------------------------
# POST /invoke
# ---------------------------------------------------------------------------


class TestInvokeEndpoint:

    def test_invoke_disabled(self, client) -> None:
        """Returns 503 when interop is disabled."""
        resp = client.post("/api/v1/interop/invoke", json={
            "caller_agent_id": "a",
            "target_listing_id": "b",
            "method": "search",
        })
        assert resp.status_code == 503

    def test_invoke_target_not_found(self, client, monkeypatch) -> None:
        _enable_interop(monkeypatch)
        resp = client.post("/api/v1/interop/invoke", json={
            "caller_agent_id": "a",
            "target_listing_id": "nonexistent",
            "method": "search",
        })
        assert resp.status_code == 404

    def test_invoke_no_endpoint(self, client, monkeypatch) -> None:
        _enable_interop(monkeypatch)
        listing = _seed_listing(endpoint_url="")

        resp = client.post("/api/v1/interop/invoke", json={
            "caller_agent_id": "a",
            "target_listing_id": listing.id,
            "method": "search",
        })
        assert resp.status_code == 422

    def test_invoke_max_cost_exceeded(self, client, monkeypatch) -> None:
        _enable_interop(monkeypatch)
        listing = _seed_listing()

        resp = client.post("/api/v1/interop/invoke", json={
            "caller_agent_id": "a",
            "target_listing_id": listing.id,
            "method": "search",
            "max_cost": 999.0,
        })
        assert resp.status_code == 422
        assert "exceeds limit" in resp.json()["detail"]

    def test_invoke_success(self, client, monkeypatch) -> None:
        """Successful invocation through the generic adapter stub."""
        _enable_interop(monkeypatch)
        listing = _seed_listing(tags=["langchain"])

        resp = client.post("/api/v1/interop/invoke", json={
            "caller_agent_id": "agent-a",
            "target_listing_id": listing.id,
            "method": "search",
            "params": {"query": "test"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["target_framework"] == "langchain"
        assert data["request_id"]


# ---------------------------------------------------------------------------
# GET /capabilities/{listing_id}
# ---------------------------------------------------------------------------


class TestCapabilitiesEndpoint:

    def test_capabilities_not_found(self, client) -> None:
        resp = client.get("/api/v1/interop/capabilities/nonexistent")
        assert resp.status_code == 404

    def test_capabilities_agent(self, client) -> None:
        listing = _seed_listing()
        resp = client.get(f"/api/v1/interop/capabilities/{listing.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["listing_id"] == listing.id
        assert "generic" in data["supported_frameworks"]

    def test_capabilities_mcp_includes_methods(self, client) -> None:
        listing = _seed_mcp_listing(supported_methods=["search", "list"])
        resp = client.get(f"/api/v1/interop/capabilities/{listing.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "search" in data["methods"]
        assert "list" in data["methods"]

    def test_capabilities_no_endpoint(self, client) -> None:
        """Listing without endpoint still returns capabilities."""
        listing = _seed_listing(endpoint_url="")
        resp = client.get(f"/api/v1/interop/capabilities/{listing.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["listing_id"] == listing.id


# ---------------------------------------------------------------------------
# POST /discover
# ---------------------------------------------------------------------------


class TestDiscoverEndpoint:

    def test_discover_finds_agents(self, client) -> None:
        _seed_listing(name="Code Generator", description="Generates code")
        _seed_listing(name="Image Classifier", description="Classifies images")

        resp = client.post("/api/v1/interop/discover", json={
            "capability_query": "code",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["capabilities"]) == 1

    def test_discover_empty_results(self, client) -> None:
        resp = client.post("/api/v1/interop/discover", json={
            "capability_query": "nonexistent",
        })
        assert resp.status_code == 200
        assert resp.json()["capabilities"] == []


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------


class TestDisputeEndpoints:

    def test_open_dispute(self, client) -> None:
        resp = client.post("/api/v1/interop/disputes", json={
            "invocation_id": "inv-1",
            "initiator": "agent-a",
            "reason": "Bad response",
            "evidence_hash": "abc123",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "open"
        assert data["invocation_id"] == "inv-1"
        assert data["id"]

    def test_get_dispute(self, client) -> None:
        create_resp = client.post("/api/v1/interop/disputes", json={
            "invocation_id": "inv-1",
            "initiator": "agent-a",
            "reason": "Bad response",
            "evidence_hash": "abc123",
        })
        dispute_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/interop/disputes/{dispute_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == dispute_id

    def test_get_dispute_not_found(self, client) -> None:
        resp = client.get("/api/v1/interop/disputes/nonexistent")
        assert resp.status_code == 404

    def test_resolve_dispute(self, client) -> None:
        create_resp = client.post("/api/v1/interop/disputes", json={
            "invocation_id": "inv-1",
            "initiator": "agent-a",
            "reason": "Bad response",
            "evidence_hash": "abc123",
        })
        dispute_id = create_resp.json()["id"]

        resp = client.put(f"/api/v1/interop/disputes/{dispute_id}/resolve", json={
            "resolution": "Refund issued",
            "resolver": "arbitrator",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"
        assert data["resolution"] == "Refund issued"
        assert data["resolved_at"] is not None

    def test_resolve_dispute_not_found(self, client) -> None:
        resp = client.put("/api/v1/interop/disputes/nonexistent/resolve", json={
            "resolution": "x",
            "resolver": "y",
        })
        assert resp.status_code == 404

    def test_resolve_already_resolved(self, client) -> None:
        create_resp = client.post("/api/v1/interop/disputes", json={
            "invocation_id": "inv-1",
            "initiator": "agent-a",
            "reason": "Bad response",
            "evidence_hash": "abc123",
        })
        dispute_id = create_resp.json()["id"]

        client.put(f"/api/v1/interop/disputes/{dispute_id}/resolve", json={
            "resolution": "Fixed",
            "resolver": "arb",
        })

        resp = client.put(f"/api/v1/interop/disputes/{dispute_id}/resolve", json={
            "resolution": "Try again",
            "resolver": "arb",
        })
        assert resp.status_code == 409
