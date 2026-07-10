"""Cost efficiency metric — calculates cost per query and scores the cost/quality ratio."""

from __future__ import annotations

import structlog

from backend.eval.metrics.base import BaseMetric
from backend.eval.models import Step, StepScore, StepType, Trajectory

logger = structlog.get_logger(__name__)

# Default model pricing (USD per 1 000 tokens) — used when per-step cost is missing.
_DEFAULT_COST_PER_1K_TOKENS: dict[str, float] = {
    "input": 0.003,
    "output": 0.012,
}


class CostEfficiencyMetric(BaseMetric):
    """Measure how efficiently the agent spends its budget.

    The metric evaluates the ratio of *useful work* to *cost incurred*:
    - Total cost is taken from ``trajectory.total_cost_usd`` (or estimated
      from token counts when cost fields are zero).
    - Useful work is proxied by the fraction of steps that produce output
      (retrieval, reasoning, answer) vs. wasted steps (guardrail blocks,
      repeated tool calls).
    - A lower cost for the same quality yields a higher score.
    """

    name = "cost_efficiency"
    description = (
        "Measures cost-effectiveness of the trajectory: useful work per dollar spent. "
        "1.0 = highly efficient, 0.0 = extremely wasteful or zero-cost (unscored)."
    )

    def __init__(
        self,
        *,
        target_cost_usd: float = 0.05,
        **config,
    ) -> None:
        super().__init__(target_cost_usd=target_cost_usd, **config)
        self.target_cost_usd = target_cost_usd

    # ------------------------------------------------------------------
    # Per-step scoring
    # ------------------------------------------------------------------

    def score_step(self, trajectory: Trajectory, step: Step) -> StepScore:
        cost = self._step_cost(trajectory, step)
        is_useful = step.step_type in _USEFUL_STEP_TYPES

        if cost == 0.0:
            # Zero-cost step — score based purely on usefulness.
            score = 1.0 if is_useful else 0.5
        else:
            # Positive cost step — usefulness contributes to score.
            score = 1.0 if is_useful else 0.2

        return StepScore(
            step_id=step.step_id,
            metric_name=self.name,
            score=round(self.clamp(score), 4),
            details=f"cost=${cost:.6f}, useful={is_useful}",
            breakdown={
                "cost_usd": round(cost, 8),
                "useful": is_useful,
                "step_type": step.step_type.value,
            },
        )

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def aggregate_steps(
        self,
        trajectory: Trajectory,
        step_scores: list[StepScore],
    ) -> float:
        """Combine per-step efficiency with trajectory-level cost ratio."""
        total_cost = self._total_cost(trajectory)
        useful_fraction = self._useful_fraction(step_scores)

        if total_cost == 0.0:
            return 0.0

        # Score = useful_fraction / normalised_cost.
        # Lower cost relative to target → higher score.
        cost_ratio = self.normalise(total_cost, 0.0, self.target_cost_usd * 5)
        # Avoid division by zero; clamp denominator.
        denominator = max(cost_ratio, 0.01)
        raw = useful_fraction / denominator

        return round(self.clamp(raw), 4)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _step_cost(trajectory: Trajectory, step: Step) -> float:
        if step.cost_usd > 0:
            return step.cost_usd
        if step.tokens_used > 0:
            # Rough estimate using default pricing.
            return step.tokens_used * _DEFAULT_COST_PER_1K_TOKENS["output"] / 1000.0
        return 0.0

    @staticmethod
    def _total_cost(trajectory: Trajectory) -> float:
        if trajectory.total_cost_usd > 0:
            return trajectory.total_cost_usd
        return sum(s.cost_usd for s in trajectory.steps)

    @staticmethod
    def _useful_fraction(step_scores: list[StepScore]) -> float:
        if not step_scores:
            return 0.0
        useful = sum(1 for s in step_scores if s.score >= 0.8)
        return useful / len(step_scores)


_USEFUL_STEP_TYPES: frozenset[StepType] = frozenset(
    {
        StepType.RETRIEVAL,
        StepType.REASONING,
        StepType.ANSWER,
        StepType.TOOL_RESULT,
    }
)
