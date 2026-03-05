"""Shared fixtures for integration tests.

Spins up a real TimescaleDB container via testcontainers and provides
the DB URL + schema-applied connection pool for all integration tests.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "src" / "blockthrough" / "pipeline" / "schema.sql"

TIMESCALEDB_IMAGE = "timescale/timescaledb:2.17.2-pg16"


# ── Mock classes for LiteLLM callback kwargs ────────────────────────────
# Defined at module level so they aren't recreated on every call to
# make_litellm_kwargs (matters for the 1000-iteration benchmark).

class _MockFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments

class _MockToolCall:
    def __init__(self, func: _MockFunction):
        self.function = func

class _MockUsage:
    def __init__(self, pt: int, ct: int):
        self.prompt_tokens = pt
        self.completion_tokens = ct

class _MockMessage:
    def __init__(self, content: str, tcs: list):
        self.content = content
        self.tool_calls = tcs

class _MockChoice:
    def __init__(self, message: _MockMessage):
        self.message = message

class _MockResponse:
    def __init__(self, usage: _MockUsage, choices: list[_MockChoice]):
        self.usage = usage
        self.choices = choices


# ── Container and DB fixtures ───────────────────────────────────────────

@pytest.fixture(scope="session")
def timescaledb_container():
    """Start a TimescaleDB container once per test session."""
    container = PostgresContainer(
        image=TIMESCALEDB_IMAGE,
        username="agentproof",
        password="testpass",
        dbname="agentproof_test",
    )
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def db_url(timescaledb_container) -> str:
    """asyncpg-compatible connection URL for the test container."""
    host = timescaledb_container.get_container_host_ip()
    port = timescaledb_container.get_exposed_port(5432)
    return f"postgresql://agentproof:testpass@{host}:{port}/agentproof_test"


@pytest.fixture(scope="session")
def sqlalchemy_db_url(db_url) -> str:
    """SQLAlchemy async URL derived from the asyncpg URL."""
    return db_url.replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def _apply_schema(db_url):
    """Apply schema.sql to the test DB once per session."""
    schema_sql = SCHEMA_PATH.read_text()

    async def _setup():
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(schema_sql)
        finally:
            await conn.close()

    asyncio.run(_setup())


@pytest.fixture(scope="session")
def _session_pool(db_url, _apply_schema):
    """Session-scoped asyncpg pool — avoids TCP reconnects per test."""
    pool = asyncio.run(asyncpg.create_pool(db_url, min_size=2, max_size=5))
    yield pool
    asyncio.run(pool.close())


@pytest.fixture
async def asyncpg_pool(_session_pool) -> asyncpg.Pool:
    """Yield the session pool for per-test use."""
    return _session_pool


@pytest.fixture
async def clean_db(asyncpg_pool: asyncpg.Pool) -> asyncpg.Pool:
    """Truncate tables before each test for isolation."""
    async with asyncpg_pool.acquire() as conn:
        await conn.execute("TRUNCATE tool_calls, llm_events CASCADE")
    return asyncpg_pool


# ── Time range fixture ──────────────────────────────────────────────────

@pytest.fixture
def wide_time_params() -> dict[str, str]:
    """Time window that captures all seeded test data."""
    start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return {"start": start, "end": end}


# ── Shared test helpers ─────────────────────────────────────────────────

async def wait_for_flush(pool: asyncpg.Pool, expected: int, timeout_s: float = 5.0) -> int:
    """Poll the DB until we see the expected event count or timeout."""
    deadline = time.monotonic() + timeout_s
    count = 0
    async with pool.acquire() as conn:
        while time.monotonic() < deadline:
            count = await conn.fetchval("SELECT COUNT(*) FROM llm_events")
            if count >= expected:
                return count
            await asyncio.sleep(0.05)
    return count


def make_callback(
    db_url: str,
    *,
    org_id: str = "test-org",
    enable_classification: bool = True,
    batch_size: int = 10,
    flush_interval_ms: int = 50,
):
    """Create an BlockThroughCallback with test-friendly defaults."""
    from blockthrough.pipeline.callback import BlockThroughCallback

    return BlockThroughCallback(
        db_url=db_url,
        org_id=org_id,
        enable_classification=enable_classification,
        batch_size=batch_size,
        flush_interval_ms=flush_interval_ms,
    )


def make_litellm_kwargs(
    *,
    model: str = "claude-sonnet-4-20250514",
    provider: str = "anthropic",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost: float = 0.0015,
    messages: list[dict] | None = None,
    tool_calls: list[dict] | None = None,
    tools: list[dict] | None = None,
    metadata: dict | None = None,
    exception: Exception | None = None,
    litellm_call_id: str | None = None,
) -> tuple[dict, object, datetime, datetime]:
    """Build mock LiteLLM callback kwargs + response object."""
    if messages is None:
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Please summarize the text."},
            {"role": "user", "content": "Hello, world!"},
        ]

    call_id = litellm_call_id or uuid.uuid4().hex

    kwargs = {
        "model": model,
        "messages": messages,
        "litellm_params": {
            "custom_llm_provider": provider,
            "metadata": metadata or {},
        },
        "response_cost": cost,
        "litellm_call_id": call_id,
    }

    if tools:
        kwargs["tools"] = tools
    if exception:
        kwargs["exception"] = exception

    mock_tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            mock_tool_calls.append(
                _MockToolCall(_MockFunction(tc["name"], tc.get("arguments", "{}")))
            )

    response_obj = _MockResponse(
        usage=_MockUsage(prompt_tokens, completion_tokens),
        choices=[_MockChoice(_MockMessage("This is the completion.", mock_tool_calls))],
    )

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(milliseconds=500)

    return kwargs, response_obj, start_time, end_time


async def seed_events(
    pool: asyncpg.Pool,
    count: int = 10,
    *,
    model: str = "claude-sonnet-4-20250514",
    provider: str = "anthropic",
    task_type: str = "code_generation",
    status: str = "success",
    trace_id: str | None = None,
    org_id: str | None = None,
    base_time: datetime | None = None,
) -> list[uuid.UUID]:
    """Insert test events directly into the DB. Returns the event IDs."""
    if base_time is None:
        base_time = datetime.now(timezone.utc) - timedelta(hours=1)

    ids = []
    rows = []
    for i in range(count):
        event_id = uuid.uuid4()
        ids.append(event_id)
        created_at = base_time + timedelta(seconds=i * 10)
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        rows.append((
            event_id, created_at, status, provider, model,
            100 + i, 50 + i, 150 + 2 * i,
            0.001 * (i + 1), 200.0 + i * 50,
            f"phash-{i}", f"chash-{i}",
            tid, f"span-{uuid.uuid4().hex[:8]}",
            False, task_type, 0.85,
            f"call-{uuid.uuid4().hex[:8]}", org_id,
        ))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO llm_events (
                id, created_at, status, provider, model,
                prompt_tokens, completion_tokens, total_tokens,
                estimated_cost, latency_ms,
                prompt_hash, completion_hash,
                trace_id, span_id,
                has_tool_calls, task_type, task_type_confidence,
                litellm_call_id, org_id
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8,
                $9, $10,
                $11, $12,
                $13, $14,
                $15, $16, $17,
                $18, $19
            )
            """,
            rows,
        )

    return ids
