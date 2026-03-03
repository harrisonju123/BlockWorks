"""API integration tests.

Spins up TimescaleDB, seeds test data, and exercises every API endpoint
through the FastAPI test client with httpx.AsyncClient.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from agentproof.api.app import app
from agentproof.api.deps import get_db

from .conftest import seed_events

pytestmark = pytest.mark.integration


@pytest.fixture
async def override_db(sqlalchemy_db_url: str, _apply_schema):
    """Override the FastAPI get_db dependency to use the test DB."""
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
async def seeded_db(clean_db: asyncpg.Pool) -> asyncpg.Pool:
    """Seed the test DB with a representative dataset."""
    base_time = datetime.now(timezone.utc) - timedelta(hours=2)

    await asyncio.gather(
        seed_events(
            clean_db, count=5,
            model="claude-sonnet-4-20250514", provider="anthropic",
            task_type="code_generation", status="success",
            trace_id="trace-alpha", org_id="org-1",
            base_time=base_time,
        ),
        seed_events(
            clean_db, count=3,
            model="gpt-4o", provider="openai",
            task_type="summarization", status="success",
            trace_id="trace-beta", org_id="org-1",
            base_time=base_time + timedelta(minutes=5),
        ),
        seed_events(
            clean_db, count=2,
            model="claude-sonnet-4-20250514", provider="anthropic",
            task_type="code_generation", status="failure",
            trace_id="trace-gamma", org_id="org-1",
            base_time=base_time + timedelta(minutes=10),
        ),
    )

    return clean_db


@pytest.fixture
async def client(override_db, seeded_db) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class TestHealthEndpoint:

    async def test_health_ok(self, client: httpx.AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["db"] == "connected"
        assert "version" in body


class TestSummaryEndpoint:

    async def test_summary_response_shape(self, client: httpx.AsyncClient):
        resp = await client.get("/api/v1/stats/summary")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("period", "total_requests", "total_cost_usd", "total_tokens", "failure_rate", "groups"):
            assert key in body

    async def test_summary_totals(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/stats/summary", params=wide_time_params)
        body = resp.json()
        assert body["total_requests"] == 10
        assert body["failure_rate"] == pytest.approx(0.2, abs=0.01)

    async def test_summary_group_by_model(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get(
            "/api/v1/stats/summary",
            params={**wide_time_params, "group_by": "model"},
        )
        keys = {g["key"] for g in resp.json()["groups"]}
        assert "claude-sonnet-4-20250514" in keys
        assert "gpt-4o" in keys

    async def test_summary_group_by_task_type(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get(
            "/api/v1/stats/summary",
            params={**wide_time_params, "group_by": "task_type"},
        )
        keys = {g["key"] for g in resp.json()["groups"]}
        assert "code_generation" in keys
        assert "summarization" in keys

    async def test_summary_stat_group_shape(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/stats/summary", params=wide_time_params)
        group = resp.json()["groups"][0]
        for field in (
            "key", "request_count", "total_cost_usd", "avg_latency_ms",
            "p95_latency_ms", "avg_cost_per_request_usd",
            "total_prompt_tokens", "total_completion_tokens", "failure_count",
        ):
            assert field in group, f"Missing field: {field}"


class TestTimeseriesEndpoint:

    async def test_timeseries_response_shape(self, client: httpx.AsyncClient):
        resp = await client.get("/api/v1/stats/timeseries")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("metric", "interval", "data"):
            assert key in body

    async def test_timeseries_cost_metric(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get(
            "/api/v1/stats/timeseries",
            params={**wide_time_params, "metric": "cost", "interval": "1h"},
        )
        body = resp.json()
        assert body["metric"] == "cost"
        assert body["interval"] == "1h"
        assert len(body["data"]) > 0
        point = body["data"][0]
        assert "timestamp" in point
        assert "value" in point

    async def test_timeseries_filter_by_model(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get(
            "/api/v1/stats/timeseries",
            params={**wide_time_params, "metric": "requests", "model": "gpt-4o"},
        )
        total = sum(p["value"] for p in resp.json()["data"])
        assert total == 3


class TestTopTracesEndpoint:

    async def test_top_traces_response_shape(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/stats/top-traces", params=wide_time_params)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["traces"]) > 0
        for field in (
            "trace_id", "total_cost_usd", "total_tokens", "total_latency_ms",
            "event_count", "models_used", "first_event_at", "last_event_at",
        ):
            assert field in body["traces"][0], f"Missing field: {field}"

    async def test_top_traces_sort_by_cost(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get(
            "/api/v1/stats/top-traces",
            params={**wide_time_params, "sort_by": "cost"},
        )
        costs = [t["total_cost_usd"] for t in resp.json()["traces"]]
        assert costs == sorted(costs, reverse=True)

    async def test_top_traces_limit(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get(
            "/api/v1/stats/top-traces",
            params={**wide_time_params, "limit": 2},
        )
        assert len(resp.json()["traces"]) <= 2


class TestWasteScoreEndpoint:

    async def test_waste_score_response_shape(self, client: httpx.AsyncClient):
        resp = await client.get("/api/v1/stats/waste-score")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("waste_score", "total_potential_savings_usd", "breakdown"):
            assert key in body


class TestEventsEndpoint:

    async def test_events_response_shape(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params=wide_time_params)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("events", "total_count", "has_more"):
            assert key in body

    async def test_events_total_count(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "limit": 50})
        assert resp.json()["total_count"] == 10

    async def test_events_detail_shape(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "limit": 1})
        event = resp.json()["events"][0]
        for field in (
            "id", "created_at", "status", "provider", "model",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "estimated_cost", "latency_ms", "trace_id", "span_id",
            "task_type", "has_tool_calls",
        ):
            assert field in event, f"Missing field: {field}"

    async def test_filter_by_model(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "model": "gpt-4o"})
        body = resp.json()
        assert body["total_count"] == 3
        for event in body["events"]:
            assert event["model"] == "gpt-4o"

    async def test_filter_by_task_type(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "task_type": "summarization"})
        body = resp.json()
        assert body["total_count"] == 3
        for event in body["events"]:
            assert event["task_type"] == "summarization"

    async def test_filter_by_status(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "status": "failure"})
        body = resp.json()
        assert body["total_count"] == 2
        for event in body["events"]:
            assert event["status"] == "failure"

    async def test_pagination(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "limit": 3, "offset": 0})
        body = resp.json()
        assert len(body["events"]) == 3
        assert body["total_count"] == 10
        assert body["has_more"] is True

        resp = await client.get("/api/v1/events", params={**wide_time_params, "limit": 3, "offset": 3})
        body = resp.json()
        assert len(body["events"]) == 3
        assert body["has_more"] is True

    async def test_filter_by_trace_id(self, client: httpx.AsyncClient, wide_time_params):
        resp = await client.get("/api/v1/events", params={**wide_time_params, "trace_id": "trace-alpha"})
        body = resp.json()
        assert body["total_count"] == 5
        for event in body["events"]:
            assert event["trace_id"] == "trace-alpha"
