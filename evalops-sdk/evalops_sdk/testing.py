"""Test utilities for EvalOps plugin authors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockTrajectory:
    """Lightweight trajectory for plugin unit tests."""

    trajectory_id: str = "test-traj-001"
    query: str = "test query"
    steps: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_step(
        self,
        *,
        step_id: int | None = None,
        input_text: str = "",
        output_text: str = "",
        step_type: str = "answer",
        tokens_used: int = 0,
    ) -> MockTrajectory:
        if step_id is None:
            step_id = len(self.steps) + 1
        self.steps.append({
            "step_id": step_id,
            "step_type": step_type,
            "input_text": input_text,
            "output_text": output_text,
            "tokens_used": tokens_used,
        })
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "query": self.query,
            "steps": self.steps,
            "final_answer": self.final_answer,
            "metadata": self.metadata,
        }


class PluginTestHarness:
    """Convenience harness for testing EvalOps plugins end-to-end."""

    def __init__(self) -> None:
        self._metric_results: list[dict[str, Any]] = []
        self._filter_results: list[dict[str, Any]] = []

    def test_metric(
        self,
        metric_fn: Any,
        input_text: str,
        output_text: str,
        *,
        expected_min: float = 0.0,
        expected_max: float = 1.0,
    ) -> dict[str, Any]:
        """Run a metric scorer and validate the result."""
        score = metric_fn(input_text, output_text)
        result = {
            "input": input_text,
            "output": output_text,
            "score": score,
            "in_range": expected_min <= score <= expected_max,
        }
        self._metric_results.append(result)
        return result

    def test_filter(
        self,
        filter_fn: Any,
        input_text: str,
        *,
        context: str = "",
        output: str = "",
        expect_blocked: bool = False,
    ) -> dict[str, Any]:
        """Run a filter and validate its decision."""
        verdict, score, details = filter_fn(input_text, context, output)
        result = {
            "input": input_text,
            "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
            "score": score,
            "details": details,
            "blocked": verdict.value == "block" if hasattr(verdict, "value") else False,
            "expected_blocked": expect_blocked,
            "correct": (verdict.value == "block") == expect_blocked if hasattr(verdict, "value") else True,
        }
        self._filter_results.append(result)
        return result

    def get_results(self) -> dict[str, Any]:
        return {
            "metric_tests": self._metric_results,
            "filter_tests": self._filter_results,
            "total_tests": len(self._metric_results) + len(self._filter_results),
        }


def assert_metric_score(
    score: float,
    *,
    expected: float | None = None,
    min_val: float = 0.0,
    max_val: float = 1.0,
    tolerance: float = 0.01,
) -> None:
    """Assert that a metric score is within expected bounds."""
    assert min_val <= score <= max_val, (
        f"Score {score} out of range [{min_val}, {max_val}]"
    )
    if expected is not None:
        assert abs(score - expected) <= tolerance, (
            f"Score {score} != expected {expected} (tolerance={tolerance})"
        )


def assert_filter_blocked(
    result: dict[str, Any],
    *,
    should_block: bool = True,
) -> None:
    """Assert that a filter result matches expected blocking behavior."""
    is_blocked = result.get("blocked", False)
    assert is_blocked == should_block, (
        f"Filter blocked={is_blocked}, expected {should_block}. "
        f"Verdict: {result.get('verdict')}, Score: {result.get('score')}"
    )
