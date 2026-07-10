"""FastAPI dependency-injection providers.

Singleton repository instances and factory functions for engines.
Import and use via ``Depends()`` in route functions.
"""

from __future__ import annotations

from backend.db.repositories import (
    EvalRepository,
    PipelineRepository,
    SweepRepository,
    TraceRepository,
)
from backend.eval.blame_attribution import BlameAttributionEngine
from backend.eval.engine import EvalEngine

# ---------------------------------------------------------------------------
# Singleton repositories (one per application lifetime)
# ---------------------------------------------------------------------------

_pipeline_repo = PipelineRepository()
_trace_repo = TraceRepository()
_eval_repo = EvalRepository()
_sweep_repo = SweepRepository()


def get_pipeline_repository() -> PipelineRepository:
    return _pipeline_repo


def get_trace_repository() -> TraceRepository:
    return _trace_repo


def get_eval_repository() -> EvalRepository:
    return _eval_repo


def get_sweep_repository() -> SweepRepository:
    return _sweep_repo


# ---------------------------------------------------------------------------
# Engine factories
# ---------------------------------------------------------------------------


def get_eval_engine() -> EvalEngine:
    """Return a default EvalEngine wired with all six core metrics."""
    return EvalEngine.default()


def get_blame_engine() -> BlameAttributionEngine:
    """Return a fresh BlameAttributionEngine instance."""
    return BlameAttributionEngine()
