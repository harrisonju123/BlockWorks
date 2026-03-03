"""Dependency injection for FastAPI routes."""

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.db.engine import get_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


def resolve_time_range(
    start: datetime | None,
    end: datetime | None,
    default_hours: int = 24,
) -> tuple[datetime, datetime]:
    """Resolve optional start/end into concrete UTC datetimes."""
    now = datetime.now(timezone.utc)
    return start or (now - timedelta(hours=default_hours)), end or now
