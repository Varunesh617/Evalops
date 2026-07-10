"""Abstract base class for evaluation metrics."""

from __future__ import annotations

import abc
from typing import Any

import structlog

from backend.eval.models import MetricResult, Step, StepScore, Trajectory

logger = structlog.get_logger(__name__)


class BaseMetric(abc.ABC):
    """Abstract base class that all evaluation metrics must implement.

    Subclasses must implement ``score_step`` for per-step scoring and
    optionally override ``aggregate_steps`` to customize how step scores
    are combined into an overall metric result.
    """

    name: str = "base"
    description: str = ""

    def __init__(self, **config: Any) -> None:
        self.config = config
        self._log = logger.bind(metric=self.name)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def evaluate(self, trajectory: Trajectory) -> MetricResult:
        """Run the metric against a full trajectory and return a result."""
        self._log.debug(
            "evaluating_trajectory",
            step_count=len(trajectory.steps),
            query=trajectory.query[:120],
        )
        step_scores: list[StepScore] = []
        for step in trajectory.steps:
            step_score = self.score_step(trajectory, step)
            step_scores.append(step_score)
        overall = self.aggregate_steps(trajectory, step_scores)
        return MetricResult(
            metric_name=self.name,
            overall_score=overall,
            step_scores=step_scores,
            details=self._build_summary(trajectory, step_scores, overall),
            metadata=self._build_metadata(trajectory, step_scores),
        )

    @abc.abstractmethod
    def score_step(self, trajectory: Trajectory, step: Step) -> StepScore:
        """Score a single step within the context of the full trajectory.

        Must return a ``StepScore`` with ``0.0 <= score <= 1.0``.
        """

    # ------------------------------------------------------------------
    # Override points for subclasses
    # ------------------------------------------------------------------

    def aggregate_steps(
        self,
        trajectory: Trajectory,
        step_scores: list[StepScore],
    ) -> float:
        """Combine per-step scores into a single overall score.

        Default: weighted mean where steps matching ``_relevant_step_types``
        carry weight 1 and others carry weight 0.25.
        """
        if not step_scores:
            return 0.0
        weights = [
            1.0 if self._is_relevant(s) else 0.25 for s in step_scores
        ]
        total = sum(s.score * w for s, w in zip(step_scores, weights))
        total_weight = sum(weights)
        return round(total / total_weight, 4) if total_weight else 0.0

    def _is_relevant(self, step_score: StepScore) -> bool:
        """Whether a step is directly relevant to this metric."""
        return True

    # ------------------------------------------------------------------
    # Common utilities
    # ------------------------------------------------------------------

    @staticmethod
    def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        """Clamp a value between *low* and *high*."""
        return max(low, min(high, value))

    @staticmethod
    def normalise(value: float, min_val: float, max_val: float) -> float:
        """Linearly normalise *value* from ``[min_val, max_val]`` → ``[0, 1]``."""
        if max_val == min_val:
            return 0.0
        return BaseMetric.clamp((value - min_val) / (max_val - min_val))

    @staticmethod
    def token_overlap(text_a: str, text_b: str) -> float:
        """Jaccard token overlap between two strings (0-1)."""
        tokens_a = set(text_a.lower().split())
        tokens_b = set(text_b.lower().split())
        if not tokens_a and not tokens_b:
            return 1.0
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    @staticmethod
    def cosine_similarity_simple(vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two equal-length vectors.

        Uses pure Python — no numpy dependency for the metric logic itself.
        """
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag_a = sum(a * a for a in vec_a) ** 0.5
        mag_b = sum(b * b for b in vec_b) ** 0.5
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _build_summary(
        self,
        trajectory: Trajectory,
        step_scores: list[StepScore],
        overall: float,
    ) -> str:
        scored = [s for s in step_scores if s.score > 0]
        skipped = len(step_scores) - len(scored)
        return (
            f"{self.name}: {overall:.4f} overall, "
            f"{len(scored)} steps scored, {skipped} skipped"
        )

    def _build_metadata(
        self,
        trajectory: Trajectory,
        step_scores: list[StepScore],
    ) -> dict[str, Any]:
        scores = [s.score for s in step_scores]
        return {
            "min_step_score": min(scores) if scores else 0.0,
            "max_step_score": max(scores) if scores else 0.0,
            "step_count": len(step_scores),
        }
