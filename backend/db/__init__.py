"""Database layer for EvalOps.

Exposes the ORM models, session management, and repository classes.
"""

from backend.db.models import (
    Base,
    EvalResult,
    Pipeline,
    PipelineRun,
    Plugin,
    Sweep,
    SweepTrial,
    Trace,
    TraceStep,
    UserConfig,
)
from backend.db.repository import (
    BaseRepository,
    EvalRepository,
    PipelineRepository,
    PluginRepository,
    RunRepository,
    SweepRepository,
    TraceRepository,
    UserConfigRepository,
)
from backend.db.session import dispose_engine, get_db_session, init_db

__all__ = [
    "Base",
    "EvalResult",
    "Pipeline",
    "PipelineRun",
    "Plugin",
    "Sweep",
    "SweepTrial",
    "Trace",
    "TraceStep",
    "UserConfig",
    "BaseRepository",
    "EvalRepository",
    "PipelineRepository",
    "PluginRepository",
    "RunRepository",
    "SweepRepository",
    "TraceRepository",
    "UserConfigRepository",
    "get_db_session",
    "init_db",
    "dispose_engine",
]
