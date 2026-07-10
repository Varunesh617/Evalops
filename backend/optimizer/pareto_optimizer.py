"""Multi-objective Pareto frontier optimizer.

Finds Pareto-optimal pipeline configurations across cost, quality, and latency,
generates frontier curve data, and supports visualization export.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import numpy as np
import optuna
import structlog
from pydantic import BaseModel, Field

from backend.optimizer.config_sweeper import (
    EvalFunction,
    EvalOutcome,
    define_search_space,
)

if TYPE_CHECKING:
    from backend.core.config import PipelineConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class ParetoPoint(BaseModel):
    """A single point on the Pareto frontier."""

    config: PipelineConfig
    cost_usd: float = Field(ge=0.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    latency_ms: float = Field(ge=0.0)
    trial_number: int


class FrontierCurve(BaseModel):
    """Data describing the Pareto frontier for visualization."""

    cost_points: list[float] = Field(default_factory=list)
    quality_points: list[float] = Field(default_factory=list)
    latency_points: list[float] = Field(default_factory=list)
    point_labels: list[str] = Field(default_factory=list)
    dominated_count: int = 0
    pareto_count: int = 0


class ParetoResult(BaseModel):
    """Full result of a Pareto frontier search."""

    pareto_front: list[ParetoPoint] = Field(default_factory=list)
    frontier_curves: dict[str, FrontierCurve] = Field(default_factory=dict)
    total_trials: int = 0
    pareto_ratio: float = 0.0
    total_duration_seconds: float = 0.0
    objective_names: list[str] = Field(default_factory=list)
    all_results: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pareto dominance helpers
# ---------------------------------------------------------------------------


def dominates(a: np.ndarray, b: np.ndarray) -> bool:
    """Return True if vector *a* Pareto-dominates *b*.

    Convention: for maximisation objectives, higher is better.
    All objectives are expected as-is (higher = better).
    """
    return bool(np.all(a >= b) and np.any(a > b))


def find_pareto_front(objectives: np.ndarray) -> list[int]:
    """Return indices of Pareto-optimal points in *objectives*.

    Parameters
    ----------
    objectives : ndarray of shape (n, d)
        Each row is a point; each column is an objective (higher = better).

    Returns
    -------
    list[int]
        Indices of the non-dominated set.
    """
    n = objectives.shape[0]
    is_dominated = np.zeros(n, dtype=bool)
    for i in range(n):
        if is_dominated[i]:
            continue
        for j in range(i + 1, n):
            if is_dominated[j]:
                continue
            if dominates(objectives[i], objectives[j]):
                is_dominated[j] = True
            elif dominates(objectives[j], objectives[i]):
                is_dominated[i] = True
                break
    return [i for i in range(n) if not is_dominated[i]]


# ---------------------------------------------------------------------------
# Visualization data builders
# ---------------------------------------------------------------------------


def build_cost_quality_curve(
    pareto_points: list[ParetoPoint],
    all_points: list[dict[str, Any]] | None = None,
) -> FrontierCurve:
    """Build a 2D frontier curve (cost vs quality)."""
    sorted_pts = sorted(pareto_points, key=lambda p: p.cost_usd)
    curve = FrontierCurve(
        cost_points=[p.cost_usd for p in sorted_pts],
        quality_points=[p.quality_score for p in sorted_pts],
        latency_points=[p.latency_ms for p in sorted_pts],
        point_labels=[f"trial-{p.trial_number}" for p in sorted_pts],
        pareto_count=len(sorted_pts),
    )
    if all_points is not None:
        curve.dominated_count = len(all_points) - len(sorted_pts)
    return curve


def build_quality_latency_curve(
    pareto_points: list[ParetoPoint],
    all_points: list[dict[str, Any]] | None = None,
) -> FrontierCurve:
    """Build a 2D frontier curve (quality vs latency)."""
    sorted_pts = sorted(pareto_points, key=lambda p: -p.quality_score)
    curve = FrontierCurve(
        quality_points=[p.quality_score for p in sorted_pts],
        latency_points=[p.latency_ms for p in sorted_pts],
        cost_points=[p.cost_usd for p in sorted_pts],
        point_labels=[f"trial-{p.trial_number}" for p in sorted_pts],
        pareto_count=len(sorted_pts),
    )
    if all_points is not None:
        curve.dominated_count = len(all_points) - len(sorted_pts)
    return curve


def build_cost_latency_curve(
    pareto_points: list[ParetoPoint],
    all_points: list[dict[str, Any]] | None = None,
) -> FrontierCurve:
    """Build a 2D frontier curve (cost vs latency)."""
    sorted_pts = sorted(pareto_points, key=lambda p: p.cost_usd)
    curve = FrontierCurve(
        cost_points=[p.cost_usd for p in sorted_pts],
        latency_points=[p.latency_ms for p in sorted_pts],
        quality_points=[p.quality_score for p in sorted_pts],
        point_labels=[f"trial-{p.trial_number}" for p in sorted_pts],
        pareto_count=len(sorted_pts),
    )
    if all_points is not None:
        curve.dominated_count = len(all_points) - len(sorted_pts)
    return curve


def export_frontier_json(pareto_result: ParetoResult) -> dict[str, Any]:
    """Export Pareto result as a JSON-serialisable dict for frontend consumption."""
    return {
        "pareto_front": [pt.model_dump(mode="json") for pt in pareto_result.pareto_front],
        "frontier_curves": {
            k: v.model_dump(mode="json") for k, v in pareto_result.frontier_curves.items()
        },
        "total_trials": pareto_result.total_trials,
        "pareto_ratio": pareto_result.pareto_ratio,
        "total_duration_seconds": pareto_result.total_duration_seconds,
        "objective_names": pareto_result.objective_names,
    }


# ---------------------------------------------------------------------------
# Main optimizer
# ---------------------------------------------------------------------------


class ParetoOptimizer:
    """Multi-objective Pareto frontier optimizer.

    Uses Optuna's multi-objective TPE sampler to explore the config space
    and returns the non-dominated set.

    Usage::

        optimizer = ParetoOptimizer(eval_fn=my_eval, n_trials=100)
        result = await optimizer.run()
        for pt in result.pareto_front:
            print(pt.cost_usd, pt.quality_score, pt.latency_ms)
    """

    def __init__(
        self,
        eval_fn: EvalFunction,
        *,
        n_trials: int = 100,
        timeout_seconds: float | None = None,
        study_name: str | None = None,
        storage: str | None = None,
        sampler: optuna.samplers.BaseSampler | None = None,
        pruner: optuna.pruners.BasePruner | None = None,
    ) -> None:
        self._eval_fn = eval_fn
        self._n_trials = n_trials
        self._timeout_seconds = timeout_seconds
        self._study_name = study_name or "evalops-pareto"
        self._storage = storage
        self._sampler = sampler or optuna.samplers.NSGAIISampler(seed=42)
        self._pruner = pruner or optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=3)

    def _create_study(self) -> optuna.Study:
        return optuna.create_study(
            study_name=self._study_name,
            storage=self._storage,
            directions=["maximize", "minimize", "minimize"],  # quality, cost, latency
            sampler=self._sampler,
            pruner=self._pruner,
            load_if_exists=True,
        )

    async def run(self) -> ParetoResult:
        """Execute the multi-objective search and return the Pareto front."""
        study = self._create_study()
        all_evals: list[dict[str, Any]] = []
        start = time.monotonic()

        log = logger.bind(study=self._study_name, n_trials=self._n_trials)
        log.info("pareto_optimizer.started")

        for trial_num in range(self._n_trials):
            trial = study.ask()
            config = define_search_space(trial)

            try:
                import anyio

                outcome = await anyio.to_thread.run_sync(
                    lambda c=config: _run_eval_fn_sync(self._eval_fn, c),
                )
            except Exception:
                logger.exception("pareto_optimizer.trial_failed", trial=trial_num)
                study.tell(trial, states=[optuna.trial.TrialState.FAIL] * 3)
                continue

            # quality (maximise), cost (minimise), latency (minimise)
            study.tell(trial, [outcome.quality_score, outcome.cost_usd, outcome.latency_ms])

            all_evals.append(
                {
                    "trial": trial_num,
                    "cost_usd": outcome.cost_usd,
                    "quality_score": outcome.quality_score,
                    "latency_ms": outcome.latency_ms,
                    "config": config.model_dump(mode="json"),
                }
            )

            log.info(
                "pareto_optimizer.trial_completed",
                trial=trial_num,
                quality=round(outcome.quality_score, 4),
                cost=round(outcome.cost_usd, 4),
                latency=round(outcome.latency_ms, 1),
            )

        total_duration = time.monotonic() - start

        # Collect Pareto-optimal trials from the study
        best_trials = study.best_trials
        objectives = np.array(
            [[t.values[0], t.values[1], t.values[2]] for t in best_trials]
        )
        pareto_indices = find_pareto_front(objectives) if len(objectives) > 0 else []

        pareto_points: list[ParetoPoint] = []
        for idx in pareto_indices:
            bt = best_trials[idx]
            config = define_search_space(bt)
            pareto_points.append(
                ParetoPoint(
                    config=config,
                    cost_usd=bt.values[1],
                    quality_score=bt.values[0],
                    latency_ms=bt.values[2],
                    trial_number=bt.number,
                )
            )

        frontier_curves = {
            "cost_quality": build_cost_quality_curve(pareto_points, all_evals),
            "quality_latency": build_quality_latency_curve(pareto_points, all_evals),
            "cost_latency": build_cost_latency_curve(pareto_points, all_evals),
        }

        total_completed = len(all_evals)
        pareto_ratio = len(pareto_points) / total_completed if total_completed > 0 else 0.0

        log.info(
            "pareto_optimizer.completed",
            total_trials=total_completed,
            pareto_points=len(pareto_points),
            pareto_ratio=round(pareto_ratio, 4),
            total_seconds=round(total_duration, 2),
        )

        return ParetoResult(
            pareto_front=pareto_points,
            frontier_curves=frontier_curves,
            total_trials=total_completed,
            pareto_ratio=pareto_ratio,
            total_duration_seconds=total_duration,
            objective_names=["quality_score", "cost_usd", "latency_ms"],
            all_results=all_evals,
        )


def _run_eval_fn_sync(eval_fn: EvalFunction, config: PipelineConfig) -> EvalOutcome:
    """Bridge async eval_fn to sync for thread pool."""
    import anyio

    async def _inner() -> EvalOutcome:
        return await eval_fn(config)

    return anyio.from_thread.run(_inner)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


async def quick_pareto(
    eval_fn: EvalFunction,
    *,
    n_trials: int = 50,
    **kwargs: Any,
) -> ParetoResult:
    """Run a quick Pareto search with sensible defaults."""
    optimizer = ParetoOptimizer(eval_fn, n_trials=n_trials, **kwargs)
    return await optimizer.run()
