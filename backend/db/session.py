"""Async SQLAlchemy session management for EvalOps.

Provides an async engine, session factory, and FastAPI dependency for
injected database sessions with automatic lifecycle management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import RetrievalConfig
from backend.db.models import Base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str | None = None) -> AsyncEngine:
    """Return (and lazily create) the global async engine.

    Parameters
    ----------
    database_url:
        Overrides the connection string.  Falls back to the default
        PostgreSQL URL derived from the retrieval config when *None*.
    """
    global _engine
    if _engine is None:
        url = database_url or _default_database_url()
        _engine = create_async_engine(
            url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory(
    database_url: str | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return (and lazily create) the session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine(database_url)
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async session and ensures cleanup.

    Usage::

        @router.get("/pipelines")
        async def list_pipelines(db: AsyncSession = Depends(get_db_session)):
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(database_url: str | None = None) -> None:
    """Create all tables (for development/testing).

    In production use Alembic migrations instead.
    """
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Dispose the global engine -- call on application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def _default_database_url() -> str:
    """Build the default async PostgreSQL URL from the retrieval config."""
    cfg = RetrievalConfig()
    sync_url = cfg.database_url.get_secret_value()
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql+psycopg://"):
        return sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return sync_url
