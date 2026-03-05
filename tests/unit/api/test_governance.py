"""Tests for the governance API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blockthrough.api.app import app
from blockthrough.api.routes.governance import reset_engine


@pytest.fixture(autouse=True)
def _clean_state():
    reset_engine()
    yield
    reset_engine()


client = TestClient(app)


class TestCreateProposal:

    def test_create_proposal(self) -> None:
        resp = client.post(
            "/api/v1/governance/proposals",
            json={
                "title": "Test Proposal",
                "description": "A test proposal",
                "proposer": "alice",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Proposal"
        assert data["status"] == "active"
        assert data["for_votes"] == 0
        assert data["against_votes"] == 0

    def test_create_multiple_proposals(self) -> None:
        for i in range(3):
            resp = client.post(
                "/api/v1/governance/proposals",
                json={
                    "title": f"Proposal {i}",
                    "description": f"Description {i}",
                    "proposer": "alice",
                },
            )
            assert resp.status_code == 201


class TestListProposals:

    def test_list_empty(self) -> None:
        resp = client.get("/api/v1/governance/proposals")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created(self) -> None:
        client.post(
            "/api/v1/governance/proposals",
            json={"title": "P1", "description": "D", "proposer": "alice"},
        )
        client.post(
            "/api/v1/governance/proposals",
            json={"title": "P2", "description": "D", "proposer": "bob"},
        )

        resp = client.get("/api/v1/governance/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


class TestCastVote:

    def test_vote_for(self) -> None:
        create_resp = client.post(
            "/api/v1/governance/proposals",
            json={"title": "P1", "description": "D", "proposer": "alice"},
        )
        proposal_id = create_resp.json()["id"]

        vote_resp = client.post(
            f"/api/v1/governance/proposals/{proposal_id}/vote",
            json={"voter": "bob", "support": "for", "weight": 100},
        )
        assert vote_resp.status_code == 200
        data = vote_resp.json()
        assert data["voter"] == "bob"
        assert data["support"] == "for"
        assert data["weight"] == 100

    def test_vote_on_nonexistent_proposal(self) -> None:
        resp = client.post(
            "/api/v1/governance/proposals/bogus-id/vote",
            json={"voter": "bob", "support": "for", "weight": 100},
        )
        assert resp.status_code == 404

    def test_double_vote_rejected(self) -> None:
        create_resp = client.post(
            "/api/v1/governance/proposals",
            json={"title": "P1", "description": "D", "proposer": "alice"},
        )
        proposal_id = create_resp.json()["id"]

        client.post(
            f"/api/v1/governance/proposals/{proposal_id}/vote",
            json={"voter": "bob", "support": "for", "weight": 100},
        )
        resp = client.post(
            f"/api/v1/governance/proposals/{proposal_id}/vote",
            json={"voter": "bob", "support": "against", "weight": 50},
        )
        assert resp.status_code == 409


class TestGetProposalDetail:

    def test_detail_includes_votes(self) -> None:
        create_resp = client.post(
            "/api/v1/governance/proposals",
            json={"title": "P1", "description": "D", "proposer": "alice"},
        )
        proposal_id = create_resp.json()["id"]

        client.post(
            f"/api/v1/governance/proposals/{proposal_id}/vote",
            json={"voter": "bob", "support": "for", "weight": 100},
        )

        resp = client.get(f"/api/v1/governance/proposals/{proposal_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_votes"] == 100
        assert len(data["votes"]) == 1
        assert data["quorum_pct"] == 10.0

    def test_detail_nonexistent_proposal(self) -> None:
        resp = client.get("/api/v1/governance/proposals/bogus-id")
        assert resp.status_code == 404
