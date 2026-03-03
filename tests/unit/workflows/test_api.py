"""Tests for workflow API endpoints.

Uses FastAPI's TestClient for synchronous request/response testing
of the workflow CRUD, validation, and execution endpoints.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentproof.api.app import app
from agentproof.api.routes.workflows import reset_workflows
from agentproof.config import get_config


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset module-level singletons before each test."""
    get_config.cache_clear()
    reset_workflows()


client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_body(
    name: str = "test-wf",
    steps: list[dict] | None = None,
) -> dict:
    if steps is None:
        steps = [
            {"id": "s1", "listing_id": "l1", "step_type": "agent"},
        ]
    return {"name": name, "steps": steps}


def _create_workflow(**kwargs) -> dict:
    resp = client.post("/api/v1/workflows", json=_create_body(**kwargs))
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# POST /workflows — create
# ---------------------------------------------------------------------------


class TestCreateWorkflow:

    def test_create_returns_201(self) -> None:
        resp = client.post("/api/v1/workflows", json=_create_body())
        assert resp.status_code == 201

    def test_create_assigns_id(self) -> None:
        data = _create_workflow()
        assert data["id"] != ""
        assert len(data["id"]) == 36

    def test_create_returns_steps(self) -> None:
        data = _create_workflow()
        assert len(data["steps"]) == 1
        assert data["steps"][0]["id"] == "s1"

    def test_create_with_description(self) -> None:
        body = _create_body()
        body["description"] = "my pipeline"
        resp = client.post("/api/v1/workflows", json=body)
        assert resp.status_code == 201
        assert resp.json()["description"] == "my pipeline"

    def test_create_multi_step_dag(self) -> None:
        steps = [
            {"id": "a", "listing_id": "l1", "step_type": "agent"},
            {"id": "b", "listing_id": "l2", "step_type": "mcp_tool", "depends_on": ["a"]},
            {"id": "c", "listing_id": "l3", "step_type": "agent", "depends_on": ["a"]},
            {"id": "d", "listing_id": "l4", "step_type": "agent", "depends_on": ["b", "c"]},
        ]
        data = _create_workflow(steps=steps)
        assert data["step_count"] == 4

    def test_create_rejects_cycle(self) -> None:
        steps = [
            {"id": "a", "listing_id": "l1", "step_type": "agent", "depends_on": ["b"]},
            {"id": "b", "listing_id": "l2", "step_type": "agent", "depends_on": ["a"]},
        ]
        resp = client.post("/api/v1/workflows", json=_create_body(steps=steps))
        assert resp.status_code == 422

    def test_create_rejects_empty_steps(self) -> None:
        resp = client.post("/api/v1/workflows", json=_create_body(steps=[]))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /workflows — list
# ---------------------------------------------------------------------------


class TestListWorkflows:

    def test_empty_list(self) -> None:
        resp = client.get("/api/v1/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["workflows"] == []

    def test_list_after_create(self) -> None:
        _create_workflow(name="wf-1")
        _create_workflow(name="wf-2")

        resp = client.get("/api/v1/workflows")
        data = resp.json()
        assert data["count"] == 2


# ---------------------------------------------------------------------------
# GET /workflows/{id}
# ---------------------------------------------------------------------------


class TestGetWorkflow:

    def test_get_existing(self) -> None:
        created = _create_workflow(name="my-workflow")
        wf_id = created["id"]

        resp = client.get(f"/api/v1/workflows/{wf_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-workflow"

    def test_get_not_found(self) -> None:
        resp = client.get("/api/v1/workflows/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /workflows/{id}/execute
# ---------------------------------------------------------------------------


class TestExecuteWorkflow:

    def test_execute_single_step(self) -> None:
        created = _create_workflow()
        wf_id = created["id"]

        resp = client.post(f"/api/v1/workflows/{wf_id}/execute")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "completed"
        assert data["steps_completed"] == 1
        assert data["total_cost"] > 0

    def test_execute_with_inputs(self) -> None:
        created = _create_workflow()
        wf_id = created["id"]

        resp = client.post(
            f"/api/v1/workflows/{wf_id}/execute",
            json={"inputs": {"prompt": "hello"}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_execute_not_found(self) -> None:
        resp = client.post("/api/v1/workflows/nonexistent/execute")
        assert resp.status_code == 404

    def test_execute_returns_step_results(self) -> None:
        steps = [
            {"id": "a", "listing_id": "l1", "step_type": "agent"},
            {"id": "b", "listing_id": "l2", "step_type": "agent"},
        ]
        created = _create_workflow(steps=steps)
        wf_id = created["id"]

        resp = client.post(f"/api/v1/workflows/{wf_id}/execute")
        data = resp.json()
        assert len(data["step_results"]) == 2
        assert data["trace_id"] != ""


# ---------------------------------------------------------------------------
# GET /workflows/executions/{id}
# ---------------------------------------------------------------------------


class TestGetExecution:

    def test_get_execution_after_run(self) -> None:
        created = _create_workflow()
        exec_resp = client.post(f"/api/v1/workflows/{created['id']}/execute")
        exec_id = exec_resp.json()["id"]

        resp = client.get(f"/api/v1/workflows/executions/{exec_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == exec_id

    def test_get_execution_not_found(self) -> None:
        resp = client.get("/api/v1/workflows/executions/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /workflows/validate
# ---------------------------------------------------------------------------


class TestValidateWorkflow:

    def test_valid_workflow(self) -> None:
        body = {
            "name": "check",
            "steps": [
                {"id": "a", "listing_id": "l1", "step_type": "agent"},
                {"id": "b", "listing_id": "l2", "step_type": "agent", "depends_on": ["a"]},
            ],
        }
        resp = client.post("/api/v1/workflows/validate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_invalid_cycle(self) -> None:
        body = {
            "name": "check",
            "steps": [
                {"id": "a", "listing_id": "l1", "step_type": "agent", "depends_on": ["b"]},
                {"id": "b", "listing_id": "l2", "step_type": "agent", "depends_on": ["a"]},
            ],
        }
        resp = client.post("/api/v1/workflows/validate", json=body)
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_invalid_missing_dep(self) -> None:
        body = {
            "name": "check",
            "steps": [
                {"id": "a", "listing_id": "l1", "step_type": "agent", "depends_on": ["ghost"]},
            ],
        }
        resp = client.post("/api/v1/workflows/validate", json=body)
        data = resp.json()
        assert data["valid"] is False
        assert any("unknown step" in e for e in data["errors"])
