"""Integration tests: Alert and budget persistence against real TimescaleDB.

Covers the full CRUD lifecycle for alert rules, budget configs, and
alert history pagination, exercising the API endpoints through the
FastAPI test client backed by a real Postgres container.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import asyncpg
import httpx

from agentproof.utils import utcnow
import pytest
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from agentproof.api.app import app
from agentproof.api.deps import get_db

pytestmark = pytest.mark.integration


# Schema files applied additively: base schema first, then alerts schema
ALERTS_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "agentproof" / "pipeline" / "schema_alerts.sql"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _apply_alerts_schema(db_url, _apply_schema):
    """Apply schema_alerts.sql on top of the base schema, once per session."""
    schema_sql = ALERTS_SCHEMA_PATH.read_text()

    async def _setup():
        conn = await asyncpg.connect(db_url)
        try:
            # Use IF NOT EXISTS semantics: the tables may already exist from a
            # previous test session when running with a persistent container.
            # Wrap in a savepoint so a "relation already exists" error
            # doesn't abort the entire transaction.
            for statement in _split_sql_statements(schema_sql):
                try:
                    await conn.execute(statement)
                except asyncpg.DuplicateObjectError:
                    pass
                except asyncpg.DuplicateTableError:
                    pass
                except Exception:
                    # Other errors (e.g., hypertable already exists) are
                    # non-fatal for idempotent test setup
                    pass
        finally:
            await conn.close()

    asyncio.run(_setup())


def _split_sql_statements(sql: str) -> list[str]:
    """Naive split on semicolons outside of string literals.

    Good enough for schema DDL; not a general SQL parser.
    """
    statements = []
    for s in sql.split(";"):
        stripped = s.strip()
        if stripped and not stripped.startswith("--"):
            statements.append(stripped + ";")
    return statements


@pytest.fixture
async def clean_alerts_db(asyncpg_pool: asyncpg.Pool, _apply_alerts_schema) -> asyncpg.Pool:
    """Truncate alert-related tables before each test for isolation."""
    async with asyncpg_pool.acquire() as conn:
        # alert_history references alert_rules, so truncate in order
        await conn.execute("TRUNCATE alert_history, budget_configs, alert_rules CASCADE")
    return asyncpg_pool


@pytest.fixture
async def override_db(sqlalchemy_db_url: str, _apply_alerts_schema):
    """Override the FastAPI get_db dependency to point at the test container."""
    engine = create_async_engine(sqlalchemy_db_url, pool_size=5, max_overflow=5)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _test_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _test_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.fixture
async def client(
    override_db, clean_alerts_db
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP test client with DB dependency overridden and tables truncated."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# Alert Rules CRUD
# ---------------------------------------------------------------------------

class TestAlertRulesCRUD:

    async def test_create_and_list(self, client: httpx.AsyncClient):
        """Create a rule, then list rules and verify it appears."""
        create_resp = await client.post("/api/v1/alerts/rules", json={
            "org_id": "org-test",
            "rule_type": "spend_threshold",
            "threshold_config": {"daily_limit_usd": 50.0},
            "channel": "slack",
            "webhook_url": "https://hooks.slack.example/test",
        })
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["org_id"] == "org-test"
        assert created["rule_type"] == "spend_threshold"
        assert created["channel"] == "slack"
        assert created["enabled"] is True
        rule_id = created["id"]

        list_resp = await client.get("/api/v1/alerts/rules", params={"org_id": "org-test"})
        assert list_resp.status_code == 200
        rules = list_resp.json()
        assert any(r["id"] == rule_id for r in rules)

    async def test_update_rule(self, client: httpx.AsyncClient):
        """Create a rule, update its threshold and channel, verify the change."""
        create_resp = await client.post("/api/v1/alerts/rules", json={
            "org_id": "org-update",
            "rule_type": "error_rate",
            "threshold_config": {"max_error_rate": 0.05},
            "channel": "email",
        })
        rule_id = create_resp.json()["id"]

        update_resp = await client.put(f"/api/v1/alerts/rules/{rule_id}", json={
            "threshold_config": {"max_error_rate": 0.10},
            "channel": "slack",
            "enabled": False,
        })
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["threshold_config"]["max_error_rate"] == 0.10
        assert updated["channel"] == "slack"
        assert updated["enabled"] is False
        # updated_at should be later than created_at
        assert updated["updated_at"] >= updated["created_at"]

    async def test_delete_rule(self, client: httpx.AsyncClient):
        """Create then delete a rule; verify it's gone on subsequent list."""
        create_resp = await client.post("/api/v1/alerts/rules", json={
            "org_id": "org-delete",
            "rule_type": "latency_p95",
            "threshold_config": {"max_p95_ms": 2000},
            "channel": "both",
        })
        rule_id = create_resp.json()["id"]

        del_resp = await client.delete(f"/api/v1/alerts/rules/{rule_id}")
        assert del_resp.status_code == 204

        # Verify it no longer appears
        list_resp = await client.get("/api/v1/alerts/rules", params={"org_id": "org-delete"})
        assert all(r["id"] != rule_id for r in list_resp.json())

    async def test_delete_nonexistent_returns_404(self, client: httpx.AsyncClient):
        resp = await client.delete("/api/v1/alerts/rules/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_update_nonexistent_returns_404(self, client: httpx.AsyncClient):
        resp = await client.put(
            "/api/v1/alerts/rules/00000000-0000-0000-0000-000000000000",
            json={"enabled": False},
        )
        assert resp.status_code == 404

    async def test_full_lifecycle(self, client: httpx.AsyncClient):
        """Create -> read -> update -> verify update -> delete -> verify deleted."""
        # Create
        resp = await client.post("/api/v1/alerts/rules", json={
            "org_id": "org-lifecycle",
            "rule_type": "anomaly_zscore",
            "threshold_config": {"z_score": 3.0},
            "channel": "email",
            "webhook_url": None,
            "enabled": True,
        })
        assert resp.status_code == 201
        rule = resp.json()
        rule_id = rule["id"]

        # Read back
        list_resp = await client.get("/api/v1/alerts/rules", params={"org_id": "org-lifecycle"})
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["id"] == rule_id

        # Update
        update_resp = await client.put(f"/api/v1/alerts/rules/{rule_id}", json={
            "threshold_config": {"z_score": 2.5},
            "enabled": False,
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["threshold_config"]["z_score"] == 2.5
        assert update_resp.json()["enabled"] is False

        # Delete
        del_resp = await client.delete(f"/api/v1/alerts/rules/{rule_id}")
        assert del_resp.status_code == 204

        # Verify deleted
        list_resp = await client.get("/api/v1/alerts/rules", params={"org_id": "org-lifecycle"})
        assert len(list_resp.json()) == 0


# ---------------------------------------------------------------------------
# Budget CRUD
# ---------------------------------------------------------------------------

class TestBudgetCRUD:

    async def test_create_and_list_budgets(self, client: httpx.AsyncClient):
        """Create a budget, then verify it appears in the list."""
        create_resp = await client.post("/api/v1/budgets", json={
            "org_id": "org-budget",
            "project_id": "proj-alpha",
            "budget_usd": 100.0,
            "period": "daily",
            "action": "alert",
        })
        assert create_resp.status_code == 201
        budget = create_resp.json()
        assert budget["org_id"] == "org-budget"
        assert budget["budget_usd"] == 100.0
        assert budget["period"] == "daily"
        assert budget["action"] == "alert"
        assert budget["current_spend"] == 0.0
        budget_id = budget["id"]

        list_resp = await client.get("/api/v1/budgets", params={"org_id": "org-budget"})
        assert list_resp.status_code == 200
        budgets = list_resp.json()
        assert any(b["id"] == budget_id for b in budgets)

    async def test_budget_status(self, client: httpx.AsyncClient):
        """After creating a budget, the status endpoint should return utilization data."""
        create_resp = await client.post("/api/v1/budgets", json={
            "org_id": "org-status",
            "budget_usd": 200.0,
            "period": "monthly",
            "action": "downgrade",
        })
        budget_id = create_resp.json()["id"]

        status_resp = await client.get(f"/api/v1/budgets/{budget_id}/status")
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["id"] == budget_id
        assert status["budget_usd"] == 200.0
        assert status["current_spend"] == 0.0
        assert status["utilization_pct"] == 0.0
        assert status["period"] == "monthly"

    async def test_budget_status_nonexistent_returns_404(self, client: httpx.AsyncClient):
        resp = await client.get("/api/v1/budgets/00000000-0000-0000-0000-000000000000/status")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Alert History Pagination
# ---------------------------------------------------------------------------

class TestAlertHistoryPagination:

    async def test_empty_history(self, client: httpx.AsyncClient):
        """With no fired alerts, history should return zero items."""
        resp = await client.get("/api/v1/alerts/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 0
        assert body["items"] == []
        assert body["has_more"] is False

    async def test_history_with_data(
        self, client: httpx.AsyncClient, clean_alerts_db: asyncpg.Pool
    ):
        """Seed alert_history rows directly and verify pagination works."""
        # First, create a rule so we have a valid rule_id FK
        rule_resp = await client.post("/api/v1/alerts/rules", json={
            "org_id": "org-history",
            "rule_type": "spend_threshold",
            "threshold_config": {"daily_limit_usd": 10.0},
            "channel": "slack",
        })
        rule_id = rule_resp.json()["id"]

        # Seed 5 alert history entries directly via asyncpg
        import uuid
        now = utcnow()
        async with clean_alerts_db.acquire() as conn:
            for i in range(5):
                await conn.execute(
                    """
                    INSERT INTO alert_history (id, rule_id, triggered_at, message, severity, resolved)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    uuid.uuid4(),
                    uuid.UUID(rule_id),
                    now - timedelta(minutes=i),
                    f"Alert {i}: spend threshold exceeded",
                    "warning",
                    False,
                )

        # Fetch page 1 (limit=2)
        resp = await client.get("/api/v1/alerts/history", params={
            "org_id": "org-history",
            "limit": 2,
            "offset": 0,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 5
        assert len(body["items"]) == 2
        assert body["has_more"] is True

        # Fetch page 2
        resp = await client.get("/api/v1/alerts/history", params={
            "org_id": "org-history",
            "limit": 2,
            "offset": 2,
        })
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["has_more"] is True

        # Fetch page 3 (only 1 item left)
        resp = await client.get("/api/v1/alerts/history", params={
            "org_id": "org-history",
            "limit": 2,
            "offset": 4,
        })
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["has_more"] is False

    async def test_history_response_shape(
        self, client: httpx.AsyncClient, clean_alerts_db: asyncpg.Pool
    ):
        """Verify that each history item has all expected fields."""
        import uuid
        now = utcnow()

        rule_resp = await client.post("/api/v1/alerts/rules", json={
            "org_id": "org-shape",
            "rule_type": "error_rate",
            "threshold_config": {"max_error_rate": 0.1},
            "channel": "email",
        })
        rule_id = rule_resp.json()["id"]

        async with clean_alerts_db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO alert_history (id, rule_id, triggered_at, message, severity, resolved)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid.uuid4(),
                uuid.UUID(rule_id),
                now,
                "Error rate spiked above 10%",
                "critical",
                False,
            )

        resp = await client.get("/api/v1/alerts/history", params={"org_id": "org-shape"})
        item = resp.json()["items"][0]
        for field in ("id", "rule_id", "triggered_at", "message", "severity", "resolved"):
            assert field in item, f"Missing field: {field}"
        assert item["severity"] == "critical"
        assert item["resolved"] is False
