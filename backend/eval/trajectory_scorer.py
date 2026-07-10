"""Step-by-step trajectory scorer.

Scores each step independently using configurable scoring functions, then
aggregates to an overall trajectory score and identifies the weakest step.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.core.config import StepStatus
from backend.core.tracer import Trajectory, TrajectoryStep

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Scoring protocol
# ---------------------------------------------------------------------------


class StepScorer(abc.ABC):
    """Strategy interface for scoring an individual pipeline step."""

    @abc.abstractmethod
    def score(self, step: TrajectoryStep, context: dict[str, Any] | None = None) -> float:
        """Return a score in [0.0, 1.0] for the given step.

        A score of 1.0 means perfect execution; 0.0 means total failure.
        """
        ...


# ---------------------------------------------------------------------------
# Built-in scorers
# ---------------------------------------------------------------------------


class StatusScorer(StepScorer):
    """Base scorer that penalises non-success statuses."""

    _PENALTIES: dict[StepStatus, float] = {
        StepStatus.SUCCESS: 1.0,
        StepStatus.SKIPPED: 0.5,
        StepStatus.PENDING: 0.0,
        StepStatus.RUNNING: 0.0,
        StepStatus.TIMED_OUT: 0.1,
        StepStatus.FAILED: 0.0,
    }

    def score(self, step: TrajectoryStep, context: dict[str, Any] | None = None) -> float:
        return self._PENALTIES.get(step.status, 0.0)


class LatencyScorer(StepScorer):
    """Scores based on latency relative to a configured budget.

    If the step completes within the budget it scores 1.0; at 2× budget it
    scores 0.5; beyond 3× it scores 0.0.
    """

    def __init__(self, budget_ms: float = 10_000) -> None:
        self._budget_ms = budget_ms

    def score(self, step: TrajectoryStep, context: dict[str, Any] | None = None) -> float:
        if step.latency_ms is None or self._budget_ms <= 0:
            return 1.0  # no data → no penalty
        ratio = step.latency_ms / self._budget_ms
        if ratio <= 1.0:
            return 1.0
        if ratio >= 3.0:
            return 0.0
        return max(0.0, 1.0 - (ratio - 1.0) / 2.0)


class MetricsScorer(StepScorer):
    """Passthrough for steps that already carry a quality score in metrics."""

    def score(self, step: TrajectoryStep, context: dict[str, Any] | None = None) -> float:
        if step.metrics.score is not None:
            return max(0.0, min(1.0, step.metrics.score))
        return 1.0  # no metric → assume OK (other scorers will catch failures)


class PayloadCompletenessScorer(StepScorer):
    """Checks that the step produced the expected payload keys."""

    EXPECTED_KEYS: dict[str, list[str]] = {
        "retrieve": ["documents", "count"],
        "rerank": ["documents"],
        "reason": ["reasoning"],
        "guardrail": ["passed"],
        "generate": ["text"],
    }

    def score(self, step: TrajectoryStep, context: dict[str, Any] | None = None) -> float:
        expected = self.EXPECTED_KEYS.get(step.step_name, [])
        if not expected:
            return 1.0
        result = step.payload.get("result", {})
        present = sum(1 for k in expected if k in result and result[k])
        return present / len(expected)


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    "status": 0.40,
    "latency": 0.20,
    "metrics": 0.25,
    "completeness": 0.15,
}


@dataclass(slots=True)
class StepScore:
    """Score breakdown for a single step."""

    step_name: str
    status: StepStatus
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class TrajectoryScore:
    """Aggregated score for an entire trajectory."""

    run_id: str
    overall_score: float
    step_scores: list[StepScore] = field(default_factory=list)
    weakest_step: str = ""
    weakest_score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "overall_score": round(self.overall_score, 4),
            "weakest_step": self.weakest_step,
            "weakest_score": round(self.weakest_score, 4),
            "steps": [
                {
                    "step": s.step_name,
                    "status": str(s.status),
                    "score": round(s.score, 4),
                    "breakdown": {k: round(v, 4) for k, v in s.breakdown.items()},
                }
                for s in self.step_scores
            ],
        }


# ---------------------------------------------------------------------------
# TrajectoryScorer — main entry point
# ---------------------------------------------------------------------------


class TrajectoryScorer:
    """Scores every step in a trajectory and produces an aggregate report.

    Parameters
    ----------
    scorers : list[StepScorer] | None
        Individual scorers to apply.  When *None*, the four built-in scorers
        are used with default weights.
    weights : dict[str, float] | None
        Override weights (must align with *scorers* ordering).  Weights are
        normalised to sum to 1.0.
    """

    def __init__(
        self,
        scorers: list[StepScorer] | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._scorers = scorers or [
            StatusScorer(),
            LatencyScorer(),
            MetricsScorer(),
            PayloadCompletenessScorer(),
        ]
        self._weights = weights or dict(_DEFAULT_WEIGHTS)
        self._normalise_weights()

    def _normalise_weights(self) -> None:
        total = sum(self._weights.values()) or 1.0
        self._weights = {k: v / total for k, v in self._weights.items()}

    def _score_step(
        self,
        step: TrajectoryStep,
    ) -> StepScore:
        """Apply all scorers to a single step and compute weighted average."""
        breakdown: dict[str, float] = {}
        score_names = list(self._weights.keys())

        for i, scorer in enumerate(self._scorers):
            name = score_names[i] if i < len(score_names) else f"scorer_{i}"
            breakdown[name] = scorer.score(step)

        # Weighted average
        weighted_sum = sum(
            breakdown[name] * self._weights.get(name, 0.0)
            for name in breakdown
        )
        total_weight = sum(
            self._weights.get(name, 0.0) for name in breakdown
        )
        overall = weighted_sum / total_weight if total_weight > 0 else 1.0

        return StepScore(
            step_name=step.step_name,
            status=step.status,
            score=round(overall, 4),
            breakdown=breakdown,
        )

    def score(self, trajectory: Trajectory) -> TrajectoryScore:
        """Score every step and produce an aggregate report."""
        step_scores: list[StepScore] = []
        weakest_name = ""
        weakest_val = 1.0

        for step in trajectory.steps:
            ss = self._score_step(step)
            step_scores.append(ss)
            if ss.score < weakest_val:
                weakest_val = ss.score
                weakest_name = ss.step_name

        # Overall: weighted average across all steps (equal weight by default)
        if step_scores:
            overall = sum(s.score for s in step_scores) / len(step_scores)
        else:
            overall = 1.0

        result = TrajectoryScore(
            run_id=trajectory.run_id,
            overall_score=round(overall, 4),
            step_scores=step_scores,
            weakest_step=weakest_name,
            weakest_score=round(weakest_val, 4),
        )

        logger.info(
            "trajectory_scored",
            run_id=trajectory.run_id,
            overall=overall,
            weakest_step=weakest_name,
            weakest_score=weakest_val,
        )
        return result
