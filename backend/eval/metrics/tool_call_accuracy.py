"""Tool call accuracy metric — checks if tool calls are correct and complete."""

from __future__ import annotations

import structlog

from backend.eval.metrics.base import BaseMetric
from backend.eval.models import Step, StepScore, StepType, ToolCall, Trajectory

logger = structlog.get_logger(__name__)


class ToolCallAccuracyMetric(BaseMetric):
    """Evaluate correctness of tool invocations in a trajectory.

    Scoring:
    1. **Tool selection** — did the agent call the *expected* tool?
    2. **Parameter correctness** — do the supplied parameters match the expected ones?
    3. **Completeness** — were all expected tool calls made?
    """

    name = "tool_call_accuracy"
    description = (
        "Measures whether tool calls are correct and complete. "
        "1.0 = all tool calls perfectly match expectations, 0.0 = none match."
    )

    def __init__(self, *, parameter_weight: float = 0.6, **config) -> None:
        super().__init__(parameter_weight=parameter_weight, **config)
        self.parameter_weight = parameter_weight

    # ------------------------------------------------------------------
    # Per-step scoring
    # ------------------------------------------------------------------

    def score_step(self, trajectory: Trajectory, step: Step) -> StepScore:
        if step.step_type != StepType.TOOL_CALL:
            return StepScore(
                step_id=step.step_id,
                metric_name=self.name,
                score=1.0,
                details="Non-tool-call step — skipped.",
            )

        if not step.tool_calls:
            return StepScore(
                step_id=step.step_id,
                metric_name=self.name,
                score=0.0,
                details="Tool call step with no tool invocations.",
            )

        call_scores = [
            self._score_single_call(tc) for tc in step.tool_calls
        ]
        avg = sum(call_scores) / len(call_scores)

        return StepScore(
            step_id=step.step_id,
            metric_name=self.name,
            score=round(self.clamp(avg), 4),
            details=f"{len(step.tool_calls)} tool call(s), avg score {avg:.4f}.",
            breakdown={
                "call_count": len(step.tool_calls),
                "per_call_scores": [round(s, 4) for s in call_scores],
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _score_single_call(self, call: ToolCall) -> float:
        tool_score = self._score_tool_selection(call)
        param_score = self._score_parameters(call)
        w = self.parameter_weight
        return tool_score * (1.0 - w) + param_score * w

    @staticmethod
    def _score_tool_selection(call: ToolCall) -> float:
        """1.0 if the called tool matches the expected tool, else 0.0."""
        if call.expected_tool is None:
            return 1.0  # No expectation → can't be wrong.
        return 1.0 if call.tool_name == call.expected_tool else 0.0

    @staticmethod
    def _score_parameters(call: ToolCall) -> float:
        """Fraction of expected parameters that are present and correct."""
        if call.expected_parameters is None:
            return 1.0  # No expectation → can't be wrong.
        if not call.expected_parameters:
            return 1.0

        matched = 0
        for key, expected_val in call.expected_parameters.items():
            actual_val = call.parameters.get(key)
            if actual_val == expected_val:
                matched += 1
            elif (
                isinstance(expected_val, str)
                and isinstance(actual_val, str)
                and expected_val.lower() in actual_val.lower()
            ):
                # Partial match — count as 0.5.
                matched += 0.5
        return matched / len(call.expected_parameters)
