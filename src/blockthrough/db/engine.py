"""Async SQLAlchemy engine and session factory.

Lazily initialized to avoid import-time DB connections.
"""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


@lru_cache
def get_engine() -> AsyncEngine:
    from blockthrough.config import get_config

    config = get_config()
    return create_async_engine(
        config.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


@lru_cache
def get_session_factory() -> sessionmaker:
    return sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
