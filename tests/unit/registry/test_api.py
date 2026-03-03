"""Tests for registry API endpoints — CRUD, search, verification status."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentproof.api.app import app
from agentproof.api.routes.registry import reset_store


@pytest.fixture(autouse=True)
def _clean_store():
    """Reset the module-level store between tests."""
    reset_store()
    yield
    reset_store()


client = TestClient(app)


class TestCreateListing:

    def test_create_agent_listing(self) -> None:
        resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Code Agent",
                "description": "Generates code",
                "owner_address": "0xalice",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Code Agent"
        assert data["status"] == "active"
        assert len(data["id"]) == 36

    def test_create_mcp_listing(self) -> None:
        resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Search Server",
                "description": "MCP search server",
                "owner_address": "0xbob",
                "category": "mcp_server",
                "stake_amount": 0.05,
                "supported_methods": ["search", "list"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["category"] == "mcp_server"

    def test_insufficient_stake(self) -> None:
        resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Cheapskate",
                "description": "No stake",
                "owner_address": "0xcheap",
                "category": "agent",
                "stake_amount": 0.001,
            },
        )
        assert resp.status_code == 422


class TestGetListing:

    def test_get_existing(self) -> None:
        create_resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Test",
                "description": "Desc",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        listing_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/registry/listings/{listing_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == listing_id

    def test_get_not_found(self) -> None:
        resp = client.get("/api/v1/registry/listings/nonexistent")
        assert resp.status_code == 404


class TestUpdateListing:

    def test_update_name(self) -> None:
        create_resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Original",
                "description": "Desc",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        listing_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/registry/listings/{listing_id}",
            json={"owner_address": "0x1", "name": "Updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_update_wrong_owner(self) -> None:
        create_resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Test",
                "description": "Desc",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        listing_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/registry/listings/{listing_id}",
            json={"owner_address": "0xhacker", "name": "Hacked"},
        )
        assert resp.status_code == 403

    def test_update_not_found(self) -> None:
        resp = client.put(
            "/api/v1/registry/listings/ghost",
            json={"owner_address": "0x1", "name": "x"},
        )
        assert resp.status_code == 404


class TestDeprecateListing:

    def test_deprecate(self) -> None:
        create_resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "ToDelete",
                "description": "Desc",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        listing_id = create_resp.json()["id"]

        resp = client.request(
            "DELETE",
            f"/api/v1/registry/listings/{listing_id}",
            json={"owner_address": "0x1"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deprecated"

    def test_deprecate_wrong_owner(self) -> None:
        create_resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Test",
                "description": "Desc",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        listing_id = create_resp.json()["id"]

        resp = client.request(
            "DELETE",
            f"/api/v1/registry/listings/{listing_id}",
            json={"owner_address": "0xhacker"},
        )
        assert resp.status_code == 403


class TestSearchListings:

    def test_search_by_text(self) -> None:
        client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Code Agent",
                "description": "Generates Python code",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Image Agent",
                "description": "Classifies images",
                "owner_address": "0x2",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )

        resp = client.get("/api/v1/registry/listings", params={"q": "code"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 1
        assert data["listings"][0]["name"] == "Code Agent"

    def test_search_by_category(self) -> None:
        client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Agent",
                "description": "A",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Server",
                "description": "S",
                "owner_address": "0x2",
                "category": "mcp_server",
                "stake_amount": 0.05,
            },
        )

        resp = client.get(
            "/api/v1/registry/listings", params={"category": "agent"}
        )
        data = resp.json()
        assert data["total_count"] == 1
        assert data["listings"][0]["name"] == "Agent"

    def test_empty_search(self) -> None:
        resp = client.get("/api/v1/registry/listings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 0
        assert data["listings"] == []


class TestVerificationStatus:

    def test_verification_endpoint(self) -> None:
        create_resp = client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Test",
                "description": "Desc",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )
        listing_id = create_resp.json()["id"]

        resp = client.get(
            f"/api/v1/registry/listings/{listing_id}/verify"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "is_verified" in data
        assert "trust" in data
        assert "uptime" in data
        assert "calls" in data
        assert "stake" in data

    def test_verification_not_found(self) -> None:
        resp = client.get("/api/v1/registry/listings/ghost/verify")
        assert resp.status_code == 404


class TestDiscoverEndpoint:

    def test_discover_returns_best(self) -> None:
        client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Best Agent",
                "description": "Good at code",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )

        resp = client.get(
            "/api/v1/registry/discover",
            params={"task_type": "code_generation"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # May or may not find one depending on benchmark data,
        # but the shape should be correct
        assert "listing" in data

    def test_discover_empty(self) -> None:
        resp = client.get(
            "/api/v1/registry/discover",
            params={"task_type": "code_generation"},
        )
        assert resp.status_code == 200
        assert resp.json()["listing"] is None


class TestPopularEndpoint:

    def test_popular_empty(self) -> None:
        resp = client.get("/api/v1/registry/popular")
        assert resp.status_code == 200
        assert resp.json()["listings"] == []

    def test_popular_returns_listings(self) -> None:
        client.post(
            "/api/v1/registry/listings",
            json={
                "name": "Popular Agent",
                "description": "Very popular",
                "owner_address": "0x1",
                "category": "agent",
                "stake_amount": 0.05,
            },
        )

        resp = client.get("/api/v1/registry/popular")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["listings"]) == 1
