"""Shared fixtures for integration tests.

Spins up a real TimescaleDB container via testcontainers and provides
the DB URL + schema-applied connection pool for all integration tests.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "src" / "agentproof" / "pipeline" / "schema.sql"

# Use the same TimescaleDB image as docker-compose.yml
TIMESCALEDB_IMAGE = "timescale/timescaledb:2.17.2-pg16"


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
def sqlalchemy_db_url(timescaledb_container) -> str:
    """SQLAlchemy async URL for the test container."""
    host = timescaledb_container.get_container_host_ip()
    port = timescaledb_container.get_exposed_port(5432)
    return f"postgresql+asyncpg://agentproof:testpass@{host}:{port}/agentproof_test"


@pytest.fixture(scope="session")
def _apply_schema(db_url):
    """Apply schema.sql to the test DB once per session.

    Uses a sync event loop since session-scoped async fixtures need
    special handling. We create a temporary loop just for schema setup.
    """
    schema_sql = SCHEMA_PATH.read_text()

    async def _setup():
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(schema_sql)
        finally:
            await conn.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_setup())
    loop.close()


@pytest.fixture
async def asyncpg_pool(db_url, _apply_schema) -> AsyncGenerator[asyncpg.Pool, None]:
    """Per-test asyncpg connection pool for direct DB queries."""
    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=5)
    yield pool
    await pool.close()


@pytest.fixture
async def clean_db(asyncpg_pool: asyncpg.Pool) -> asyncpg.Pool:
    """Truncate tables before each test for isolation."""
    async with asyncpg_pool.acquire() as conn:
        await conn.execute("TRUNCATE tool_calls, llm_events CASCADE")
    return asyncpg_pool


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
    """Build mock LiteLLM callback kwargs + response object.

    Returns (kwargs, response_obj, start_time, end_time) matching
    the signature of AgentProofCallback.async_log_success_event.
    """
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

    # Build a mock response object with the structure LiteLLM returns
    class _Function:
        def __init__(self, name: str, arguments: str):
            self.name = name
            self.arguments = arguments

    class _ToolCall:
        def __init__(self, func: _Function):
            self.function = func

    class _Usage:
        def __init__(self, pt: int, ct: int):
            self.prompt_tokens = pt
            self.completion_tokens = ct

    class _Message:
        def __init__(self, content: str, tcs: list):
            self.content = content
            self.tool_calls = tcs

    class _Choice:
        def __init__(self, message: _Message):
            self.message = message

    class _Response:
        def __init__(self, usage: _Usage, choices: list[_Choice]):
            self.usage = usage
            self.choices = choices

    mock_tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            mock_tool_calls.append(
                _ToolCall(_Function(tc["name"], tc.get("arguments", "{}")))
            )

    response_obj = _Response(
        usage=_Usage(prompt_tokens, completion_tokens),
        choices=[
            _Choice(_Message("This is the completion.", mock_tool_calls))
        ],
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
    async with pool.acquire() as conn:
        for i in range(count):
            event_id = uuid.uuid4()
            ids.append(event_id)
            created_at = base_time + timedelta(seconds=i * 10)
            tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

            await conn.execute(
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
                event_id,
                created_at,
                status,
                provider,
                model,
                100 + i,
                50 + i,
                150 + 2 * i,
                0.001 * (i + 1),
                200.0 + i * 50,
                f"phash-{i}",
                f"chash-{i}",
                tid,
                f"span-{uuid.uuid4().hex[:8]}",
                False,
                task_type,
                0.85,
                f"call-{uuid.uuid4().hex[:8]}",
                org_id,
            )

    return ids
