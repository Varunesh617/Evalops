"""FastAPI dependency-injection providers.

Factory pattern: if ``DATABASE_URL`` is set the SQLAlchemy-backed repositories
are used; otherwise the in-memory repositories are created as singletons.

Both implementations share the same interface so route handlers are unaware
of which backend is active.
"""

from __future__ import annotations

import os
from typing import Any

from backend.eval.blame_attribution import BlameAttributionEngine
from backend.eval.engine import EvalEngine

# ---------------------------------------------------------------------------
# Determine which repository implementation to use
# ---------------------------------------------------------------------------

_database_url: str | None = os.environ.get("DATABASE_URL")

# In-memory singletons (used only when DATABASE_URL is *not* set)
_in_memory: dict[str, Any] = {}
if not _database_url:
    from backend.db.in_memory_repositories import (
        EvalRepository as _InMemEvalRepo,
    )
    from backend.db.in_memory_repositories import (
        PipelineRepository as _InMemPipelineRepo,
    )
    from backend.db.in_memory_repositories import (
        SweepRepository as _InMemSweepRepo,
    )
    from backend.db.in_memory_repositories import (
        TraceRepository as _InMemTraceRepo,
    )

    _in_memory = {
        "pipeline": _InMemPipelineRepo(),
        "trace": _InMemTraceRepo(),
        "eval": _InMemEvalRepo(),
        "sweep": _InMemSweepRepo(),
    }


# ---------------------------------------------------------------------------
# SQLAlchemy-backed providers (used when DATABASE_URL *is* set)
# ---------------------------------------------------------------------------

async def _sqlalchemy_pipeline_repo():
    from backend.db.repository import PipelineRepository
    from backend.db.session import get_session_factory

    factory = get_session_factory(database_url=_database_url)
    async with factory() as session:
        try:
            yield PipelineRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _sqlalchemy_trace_repo():
    from backend.db.repository import TraceRepository
    from backend.db.session import get_session_factory

    factory = get_session_factory(database_url=_database_url)
    async with factory() as session:
        try:
            yield TraceRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _sqlalchemy_eval_repo():
    from backend.db.repository import EvalRepository
    from backend.db.session import get_session_factory

    factory = get_session_factory(database_url=_database_url)
    async with factory() as session:
        try:
            yield EvalRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _sqlalchemy_sweep_repo():
    from backend.db.repository import SweepRepository
    from backend.db.session import get_session_factory

    factory = get_session_factory(database_url=_database_url)
    async with factory() as session:
        try:
            yield SweepRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Public dependency providers — the route handlers depend on these.
# ---------------------------------------------------------------------------

if _database_url:
    get_pipeline_repository = _sqlalchemy_pipeline_repo  # type: ignore[assignment]
    get_trace_repository = _sqlalchemy_trace_repo  # type: ignore[assignment]
    get_eval_repository = _sqlalchemy_eval_repo  # type: ignore[assignment]
    get_sweep_repository = _sqlalchemy_sweep_repo  # type: ignore[assignment]
else:
    from backend.db.in_memory_repositories import (
        EvalRepository,
        PipelineRepository,
        SweepRepository,
        TraceRepository,
    )

    def get_pipeline_repository() -> PipelineRepository:
        return _in_memory["pipeline"]  # type: ignore[return-value]

    def get_trace_repository() -> TraceRepository:
        return _in_memory["trace"]  # type: ignore[return-value]

    def get_eval_repository() -> EvalRepository:
        return _in_memory["eval"]  # type: ignore[return-value]

    def get_sweep_repository() -> SweepRepository:
        return _in_memory["sweep"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Engine factories
# ---------------------------------------------------------------------------


def get_eval_engine() -> EvalEngine:
    """Return a default EvalEngine wired with all six core metrics."""
    return EvalEngine.default()


def get_blame_engine() -> BlameAttributionEngine:
    """Return a fresh BlameAttributionEngine instance."""
    return BlameAttributionEngine()
