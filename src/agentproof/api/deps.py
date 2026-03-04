"""Dependency injection for FastAPI routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.db.engine import get_session_factory
from agentproof.utils import utcnow


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Standalone async context manager for use outside of FastAPI DI (e.g. background tasks)."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


def resolve_time_range(
    start: datetime | None,
    end: datetime | None,
    default_hours: int = 24,
) -> tuple[datetime, datetime]:
    """Resolve optional start/end into concrete UTC datetimes."""
    now = utcnow()
    return start or (now - timedelta(hours=default_hours)), end or now
