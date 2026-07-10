"""Pluggable evaluation engine — dispatches trajectories to configured metrics."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Sequence

import structlog

from backend.eval.metrics import METRIC_REGISTRY, BaseMetric, get_metric
from backend.eval.models import EvalResult, MetricResult, Trajectory

logger = structlog.get_logger(__name__)


class EvalEngine:
    """Dispatch a trajectory through a set of metrics and collect results.

    Usage::

        engine = EvalEngine(metrics=["faithfulness", "context_relevance"])
        result = await engine.run(trajectory)
        print(result.aggregate_score)
    """

    def __init__(
        self,
        metrics: Sequence[str | BaseMetric] | None = None,
        *,
        parallel: bool = True,
    ) -> None:
        self._parallel = parallel
        self._metrics: list[BaseMetric] = self._resolve_metrics(metrics or [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, trajectory: Trajectory) -> EvalResult:
        """Evaluate *trajectory* against all configured metrics."""
        self._log_run_start(trajectory)
        t0 = time.perf_counter()

        if self._parallel:
            results = await self._run_parallel(trajectory)
        else:
            results = await self._run_sequential(trajectory)

        elapsed = time.perf_counter() - t0
        self._log_run_done(trajectory, results, elapsed)

        return EvalResult(
            trajectory_id=trajectory.trajectory_id,
            metric_results=results,
        )

    async def run_single(
        self,
        trajectory: Trajectory,
        metric_name: str,
    ) -> MetricResult:
        """Run a single named metric against *trajectory*."""
        metric = self._find_metric(metric_name)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, metric.evaluate, trajectory)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_metrics(
        self,
        metrics: Sequence[str | BaseMetric],
    ) -> list[BaseMetric]:
        resolved: list[BaseMetric] = []
        for m in metrics:
            if isinstance(m, BaseMetric):
                resolved.append(m)
            elif isinstance(m, str):
                resolved.append(get_metric(m))
            else:
                raise TypeError(f"Unsupported metric type: {type(m)}")
        return resolved

    def _find_metric(self, name: str) -> BaseMetric:
        for m in self._metrics:
            if m.name == name:
                return m
        raise ValueError(
            f"Metric '{name}' not in engine. "
            f"Available: {[m.name for m in self._metrics]}"
        )

    async def _run_parallel(self, trajectory: Trajectory) -> list[MetricResult]:
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, m.evaluate, trajectory)
            for m in self._metrics
        ]
        return list(await asyncio.gather(*tasks))

    async def _run_sequential(self, trajectory: Trajectory) -> list[MetricResult]:
        results: list[MetricResult] = []
        for m in self._metrics:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, m.evaluate, trajectory)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_run_start(self, trajectory: Trajectory) -> None:
        logger.info(
            "eval_run_started",
            trajectory_id=trajectory.trajectory_id,
            metrics=[m.name for m in self._metrics],
            step_count=len(trajectory.steps),
        )

    def _log_run_done(
        self,
        trajectory: Trajectory,
        results: list[MetricResult],
        elapsed: float,
    ) -> None:
        scores = {r.metric_name: round(r.overall_score, 4) for r in results}
        logger.info(
            "eval_run_completed",
            trajectory_id=trajectory.trajectory_id,
            scores=scores,
            aggregate=round(
                sum(r.overall_score for r in results) / len(results), 4
            )
            if results
            else 0.0,
            elapsed_ms=round(elapsed * 1000, 1),
        )

    # ------------------------------------------------------------------
    # Class methods for convenience
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> EvalEngine:
        """Create an engine with all six core metrics."""
        return cls(metrics=list(METRIC_REGISTRY.keys()))

    @classmethod
    def from_names(
        cls,
        names: Sequence[str],
        parallel: bool = True,
    ) -> EvalEngine:
        """Create an engine from a list of metric names."""
        return cls(metrics=list(names), parallel=parallel)
