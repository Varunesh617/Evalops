"""Trajectory coherence metric — checks if reasoning steps follow logically."""

from __future__ import annotations

import structlog

from backend.eval.metrics.base import BaseMetric
from backend.eval.models import Step, StepScore, StepType, Trajectory

logger = structlog.get_logger(__name__)

# Canonical step ordering for a well-formed trajectory.
_STEP_ORDER: list[StepType] = [
    StepType.QUERY,
    StepType.RETRIEVAL,
    StepType.TOOL_CALL,
    StepType.TOOL_RESULT,
    StepType.REASONING,
    StepType.GUARDRAIL_CHECK,
    StepType.GUARDRAIL_BLOCK,
    StepType.ANSWER,
]

_ORDER_INDEX: dict[StepType, int] = {t: i for i, t in enumerate(_STEP_ORDER)}


class TrajectoryCoherenceMetric(BaseMetric):
    """Evaluate logical consistency and ordering of reasoning steps.

    Scoring factors:
    1. **Ordering** — each step's type should generally follow the previous
       step's type in the canonical order (small backward jumps are tolerable).
    2. **Completeness** — a good trajectory has retrieval → reasoning → answer.
    3. **Non-redundancy** — no immediately repeated step types with identical content.
    """

    name = "trajectory_coherence"
    description = (
        "Measures logical flow and consistency of the reasoning trajectory. "
        "1.0 = perfectly ordered and complete, 0.0 = incoherent or malformed."
    )

    def __init__(self, *, backward_penalty: float = 0.15, **config) -> None:
        super().__init__(backward_penalty=backward_penalty, **config)
        self.backward_penalty = backward_penalty

    # ------------------------------------------------------------------
    # Per-step scoring
    # ------------------------------------------------------------------

    def score_step(self, trajectory: Trajectory, step: Step) -> StepScore:
        step_idx = step.step_id
        prev_step = self._prev_step(trajectory, step_idx)

        order_score = self._score_order(prev_step, step)
        content_score = self._score_content_repetition(trajectory, step)

        combined = round(
            self.clamp(0.6 * order_score + 0.4 * content_score), 4
        )

        return StepScore(
            step_id=step.step_id,
            metric_name=self.name,
            score=combined,
            details=(
                f"order={order_score:.2f}, content_repeat={content_score:.2f}"
            ),
            breakdown={
                "order_score": round(order_score, 4),
                "content_repeat_score": round(content_score, 4),
                "step_type": step.step_type.value,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _prev_step(trajectory: Trajectory, step_id: int) -> Step | None:
        """Return the step immediately before *step_id*, or None."""
        for s in trajectory.steps:
            if s.step_id == step_id - 1:
                return s
        return None

    def _score_order(self, prev: Step | None, current: Step) -> float:
        """Score how well *current* follows *prev* in canonical order."""
        if prev is None:
            return 1.0
        prev_order = _ORDER_INDEX.get(prev.step_type, 0)
        curr_order = _ORDER_INDEX.get(current.step_type, 0)
        diff = curr_order - prev_order
        if diff >= 0:
            return 1.0
        # Backward jump — penalise proportionally.
        return self.clamp(1.0 + diff * self.backward_penalty)

    def _score_content_repetition(
        self,
        trajectory: Trajectory,
        step: Step,
    ) -> float:
        """Penalise if an identical or near-identical step appeared recently."""
        recent = [
            s
            for s in trajectory.steps
            if s.step_id < step.step_id and s.step_id >= step.step_id - 3
        ]
        for prev in recent:
            if prev.step_type == step.step_type:
                overlap = self.token_overlap(prev.output_text, step.output_text)
                if overlap > 0.85:
                    return self.clamp(1.0 - overlap)
        return 1.0
