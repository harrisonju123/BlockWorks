"""Tests for the trust score API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentproof.api.app import app
from agentproof.api.routes.trust import get_registry, reset_registry


@pytest.fixture(autouse=True)
def _clean_state():
    reset_registry()
    yield
    reset_registry()


client = TestClient(app)


def _register_agent(agent_id: str) -> None:
    """Helper to register an agent directly via the registry."""
    get_registry().register_agent(agent_id)


class TestGetTrustScore:

    def test_get_registered_agent(self) -> None:
        _register_agent("agent-1")
        resp = client.get("/api/v1/trust/agent-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "agent-1"
        assert abs(data["reliability"] - 0.5) < 0.01
        assert abs(data["composite_score"] - 0.5) < 0.01

    def test_get_unregistered_agent(self) -> None:
        resp = client.get("/api/v1/trust/ghost")
        assert resp.status_code == 404


class TestGetTopAgents:

    def test_top_agents_empty(self) -> None:
        resp = client.get("/api/v1/trust/top")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"] == []
        assert data["total_count"] == 0

    def test_top_agents_with_data(self) -> None:
        registry = get_registry()
        registry.register_agent("low")
        registry.register_agent("high")
        from agentproof.trust.types import TrustDimension
        registry.update_score("high", TrustDimension.RELIABILITY, 0.99)
        registry.update_score("low", TrustDimension.RELIABILITY, 0.1)

        resp = client.get("/api/v1/trust/top?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) == 2
        assert data["agents"][0]["agent_id"] == "high"
        assert data["total_count"] == 2


class TestUpdateTrustScore:

    def test_update_dimension(self) -> None:
        _register_agent("agent-1")
        resp = client.post(
            "/api/v1/trust/agent-1/update",
            json={
                "dimension": "reliability",
                "value": 0.95,
                "reason": "uptime improved",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert abs(data["reliability"] - 0.95) < 0.001

    def test_update_recomputes_composite(self) -> None:
        _register_agent("agent-1")
        resp = client.post(
            "/api/v1/trust/agent-1/update",
            json={"dimension": "reliability", "value": 1.0},
        )
        data = resp.json()
        # With reliability=1.0 and others at 0.5, composite should be > 0.5
        assert data["composite_score"] > 0.5

    def test_update_unregistered_agent(self) -> None:
        resp = client.post(
            "/api/v1/trust/ghost/update",
            json={"dimension": "reliability", "value": 0.9},
        )
        assert resp.status_code == 404

    def test_update_invalid_dimension(self) -> None:
        _register_agent("agent-1")
        resp = client.post(
            "/api/v1/trust/agent-1/update",
            json={"dimension": "invalid_dim", "value": 0.9},
        )
        assert resp.status_code == 422

    def test_update_value_out_of_range(self) -> None:
        _register_agent("agent-1")
        resp = client.post(
            "/api/v1/trust/agent-1/update",
            json={"dimension": "reliability", "value": 1.5},
        )
        assert resp.status_code == 422
