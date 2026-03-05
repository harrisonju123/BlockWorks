"""Tests for the enterprise API endpoints via FastAPI test client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blockthrough.api.app import app
from blockthrough.api.routes.enterprise import reset_stores
from blockthrough.config import get_config


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset in-memory stores before each test."""
    get_config.cache_clear()
    reset_stores()
    yield
    reset_stores()


@pytest.fixture
def client():
    return TestClient(app)


def _create_tenant(client: TestClient, name: str = "Test Org", plan: str = "free") -> dict:
    resp = client.post("/api/v1/enterprise/tenants", json={"name": name, "plan": plan})
    assert resp.status_code == 201
    return resp.json()


def _add_user(
    client: TestClient,
    tenant_id: str,
    email: str = "user@co.com",
    name: str = "User",
    role: str = "viewer",
) -> dict:
    resp = client.post(
        f"/api/v1/enterprise/tenants/{tenant_id}/users",
        json={"email": email, "name": name, "role": role},
    )
    assert resp.status_code == 201
    return resp.json()


class TestTenantEndpoints:
    def test_create_tenant(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/enterprise/tenants",
            json={"name": "Acme Corp", "plan": "pro"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Acme Corp"
        assert data["plan"] == "pro"
        assert data["slug"] == "acme-corp"
        assert data["is_active"] is True

    def test_list_tenants(self, client: TestClient) -> None:
        _create_tenant(client, "Org A")
        _create_tenant(client, "Org B")
        resp = client.get("/api/v1/enterprise/tenants")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_tenant(self, client: TestClient) -> None:
        t = _create_tenant(client, "Find Me")
        resp = client.get(f"/api/v1/enterprise/tenants/{t['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Find Me"

    def test_get_nonexistent_tenant(self, client: TestClient) -> None:
        resp = client.get("/api/v1/enterprise/tenants/no-such-id")
        assert resp.status_code == 404

    def test_update_tenant(self, client: TestClient) -> None:
        t = _create_tenant(client, "Old Name")
        resp = client.put(
            f"/api/v1/enterprise/tenants/{t['id']}",
            json={"name": "New Name", "plan": "enterprise"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Name"
        assert data["plan"] == "enterprise"
        assert data["slug"] == "new-name"

    def test_update_nonexistent_tenant(self, client: TestClient) -> None:
        resp = client.put(
            "/api/v1/enterprise/tenants/no-such-id",
            json={"name": "Nope"},
        )
        assert resp.status_code == 404


class TestUserEndpoints:
    def test_add_user(self, client: TestClient) -> None:
        t = _create_tenant(client)
        resp = client.post(
            f"/api/v1/enterprise/tenants/{t['id']}/users",
            json={"email": "alice@co.com", "name": "Alice", "role": "admin"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "alice@co.com"
        assert data["role"] == "admin"
        assert data["tenant_id"] == t["id"]

    def test_add_user_to_nonexistent_tenant(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/enterprise/tenants/ghost/users",
            json={"email": "a@b.com", "name": "A"},
        )
        assert resp.status_code == 404

    def test_list_users(self, client: TestClient) -> None:
        t = _create_tenant(client)
        _add_user(client, t["id"], "a@co.com", "A")
        _add_user(client, t["id"], "b@co.com", "B")
        resp = client.get(f"/api/v1/enterprise/tenants/{t['id']}/users")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_users_nonexistent_tenant(self, client: TestClient) -> None:
        resp = client.get("/api/v1/enterprise/tenants/ghost/users")
        assert resp.status_code == 404

    def test_update_role(self, client: TestClient) -> None:
        t = _create_tenant(client)
        u = _add_user(client, t["id"])
        resp = client.put(
            f"/api/v1/enterprise/tenants/{t['id']}/users/{u['id']}/role",
            json={"role": "editor"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "editor"

    def test_update_role_nonexistent(self, client: TestClient) -> None:
        t = _create_tenant(client)
        resp = client.put(
            f"/api/v1/enterprise/tenants/{t['id']}/users/ghost/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 404

    def test_remove_user(self, client: TestClient) -> None:
        t = _create_tenant(client)
        u = _add_user(client, t["id"])
        resp = client.delete(
            f"/api/v1/enterprise/tenants/{t['id']}/users/{u['id']}"
        )
        assert resp.status_code == 204

        # Verify user is gone
        resp = client.get(f"/api/v1/enterprise/tenants/{t['id']}/users")
        assert len(resp.json()) == 0

    def test_remove_nonexistent_user(self, client: TestClient) -> None:
        t = _create_tenant(client)
        resp = client.delete(
            f"/api/v1/enterprise/tenants/{t['id']}/users/ghost"
        )
        assert resp.status_code == 404


class TestAuditExportEndpoint:
    def test_trigger_audit_export_json(self, client: TestClient) -> None:
        t = _create_tenant(client)
        resp = client.post(
            f"/api/v1/enterprise/tenants/{t['id']}/audit-export",
            json={"framework": "soc2", "format": "json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tenant" in data
        assert "compliance" in data
        assert "report" in data

    def test_trigger_audit_export_csv(self, client: TestClient) -> None:
        t = _create_tenant(client)
        resp = client.post(
            f"/api/v1/enterprise/tenants/{t['id']}/audit-export",
            json={"framework": "soc2", "format": "csv"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "csv"
        assert data["size_bytes"] > 0

    def test_audit_export_nonexistent_tenant(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/enterprise/tenants/ghost/audit-export",
            json={"framework": "soc2"},
        )
        assert resp.status_code == 404


class TestUsageEndpoint:
    def test_get_usage_free(self, client: TestClient) -> None:
        t = _create_tenant(client, plan="free")
        resp = client.get(f"/api/v1/enterprise/tenants/{t['id']}/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "free"
        assert data["request_limit"] == 50_000
        assert data["current_usage"] == 0
        assert data["utilization_pct"] == 0.0

    def test_get_usage_enterprise(self, client: TestClient) -> None:
        t = _create_tenant(client, plan="enterprise")
        resp = client.get(f"/api/v1/enterprise/tenants/{t['id']}/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_limit"] is None
        assert data["utilization_pct"] is None

    def test_get_usage_nonexistent_tenant(self, client: TestClient) -> None:
        resp = client.get("/api/v1/enterprise/tenants/ghost/usage")
        assert resp.status_code == 404
