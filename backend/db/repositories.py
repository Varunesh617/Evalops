"""Backward-compatible re-export from the in-memory implementation.

The in-memory repositories live in :mod:`backend.db.in_memory_repositories`.
This module re-exports them so that existing ``from backend.db.repositories
import ...`` statements continue to work without changes.
"""

from backend.db.in_memory_repositories import (  # noqa: F401
    EvalRepository,
    PipelineRepository,
    SweepRepository,
    TraceRepository,
)

# SQLAlchemy-backed repositories (require a session); re-exported so that
# ``from backend.db.repositories import AppliedRecommendationRepository`` works.
from backend.db.repository import (  # noqa: F401
    AppliedRecommendationRepository,
)

__all__ = [
    "EvalRepository",
    "PipelineRepository",
    "SweepRepository",
    "TraceRepository",
    "AppliedRecommendationRepository",
]
