"""Tests for the evaluation engine in backend.eval.engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.eval.engine import EvalEngine
from backend.eval.metrics import METRIC_REGISTRY
from backend.eval.metrics.base import BaseMetric
from backend.eval.models import (
    EvalResult,
    MetricResult,
    Step,
    StepScore,
    StepType,
    Trajectory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trajectory(n_steps: int = 3) -> Trajectory:
    steps = []
    step_types = [StepType.QUERY, StepType.RETRIEVAL, StepType.ANSWER]
    for i in range(n_steps):
        steps.append(
            Step(step_id=i, step_type=step_types[i % len(step_types)])
        )
    return Trajectory(
        trajectory_id="test-traj",
        query="test query",
        steps=steps,
    )


class _DummyMetric(BaseMetric):
    name = "dummy"
    description = "A dummy metric for testing"

    def __init__(self, score: float = 0.5):
        super().__init__()
        self._fixed_score = score

    def score_step(self, trajectory, step):
        return StepScore(
            step_id=step.step_id,
            metric_name=self.name,
            score=self._fixed_score,
            details="dummy score",
        )


# ---------------------------------------------------------------------------
# EvalEngine tests
# ---------------------------------------------------------------------------


class TestEvalEngine:
    @pytest.mark.asyncio
    async def test_run_with_single_metric(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.8)])
        traj = _make_trajectory()
        result = await engine.run(traj)
        assert isinstance(result, EvalResult)
        assert len(result.metric_results) == 1
        assert result.metric_results[0].metric_name == "dummy"
        assert result.aggregate_score > 0

    @pytest.mark.asyncio
    async def test_run_with_multiple_metrics(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.8), _DummyMetric(0.6)])
        traj = _make_trajectory()
        result = await engine.run(traj)
        assert len(result.metric_results) == 2
        assert result.aggregate_score > 0

    @pytest.mark.asyncio
    async def test_run_sequential(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.8)], parallel=False)
        traj = _make_trajectory()
        result = await engine.run(traj)
        assert len(result.metric_results) == 1

    @pytest.mark.asyncio
    async def test_run_empty_metrics(self):
        engine = EvalEngine(metrics=[])
        traj = _make_trajectory()
        result = await engine.run(traj)
        assert len(result.metric_results) == 0

    @pytest.mark.asyncio
    async def test_run_single_metric(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.7), _DummyMetric(0.3)])
        traj = _make_trajectory()
        result = await engine.run_single(traj, "dummy")
        assert isinstance(result, MetricResult)
        assert result.metric_name == "dummy"

    @pytest.mark.asyncio
    async def test_run_single_metric_not_found(self):
        engine = EvalEngine(metrics=[_DummyMetric()])
        traj = _make_trajectory()
        with pytest.raises(ValueError, match="not in engine"):
            await engine.run_single(traj, "nonexistent")

    def test_resolve_metrics_by_name(self):
        engine = EvalEngine(metrics=["faithfulness"])
        assert len(engine._metrics) == 1
        assert engine._metrics[0].name == "faithfulness"

    def test_resolve_metrics_by_instance(self):
        m = _DummyMetric(0.5)
        engine = EvalEngine(metrics=[m])
        assert engine._metrics[0] is m

    def test_resolve_metrics_invalid_type(self):
        with pytest.raises(TypeError, match="Unsupported metric type"):
            EvalEngine(metrics=[42])  # type: ignore[list-item]

    def test_resolve_metrics_unknown_name(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            EvalEngine(metrics=["nonexistent_metric"])

    @pytest.mark.asyncio
    async def test_run_parallel_mode(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.9)], parallel=True)
        traj = _make_trajectory()
        result = await engine.run(traj)
        assert len(result.metric_results) == 1

    def test_default_classmethod(self):
        engine = EvalEngine.default()
        assert len(engine._metrics) == len(METRIC_REGISTRY)

    def test_from_names_classmethod(self):
        engine = EvalEngine.from_names(["faithfulness", "context_relevance"], parallel=False)
        assert len(engine._metrics) == 2
        assert engine._parallel is False

    @pytest.mark.asyncio
    async def test_result_aggregate_score(self):
        engine = EvalEngine(metrics=[_DummyMetric(1.0)])
        traj = _make_trajectory()
        result = await engine.run(traj)
        assert result.aggregate_score == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_result_scores_property(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.7)])
        traj = _make_trajectory()
        result = await engine.run(traj)
        scores = result.scores
        assert "dummy" in scores
        assert scores["dummy"] > 0

    @pytest.mark.asyncio
    async def test_evaluate_batch_returns_ordered_results(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.9)])
        trajs = [_make_trajectory(n_steps=3) for _ in range(3)]
        results = await engine.evaluate_batch(trajs)
        assert len(results) == 3
        assert all(isinstance(r, EvalResult) for r in results)
        assert [r.trajectory_id for r in results] == [t.trajectory_id for t in trajs]

    @pytest.mark.asyncio
    async def test_evaluate_batch_empty(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.9)])
        results = await engine.evaluate_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_evaluate_batch_concurrency_preserved(self):
        engine = EvalEngine(metrics=[_DummyMetric(0.5)], parallel=False)
        trajs = [_make_trajectory(n_steps=3) for _ in range(4)]
        results = await engine.evaluate_batch(trajs, max_concurrency=1)
        assert len(results) == 4
        assert all(r.aggregate_score > 0 for r in results)

    @pytest.mark.asyncio
    async def test_pure_logic_metrics_run_inline(self, monkeypatch):
        # cpu_bound=False metrics are run inline (no thread pool). Verify the
        # executor is never touched for a pure-logic-only engine.
        from backend.eval.metrics.trajectory_coherence import TrajectoryCoherenceMetric

        executor_calls = []

        def _fake_run_in_executor(executor, fn, *args):
            executor_calls.append((fn, args))

            class _Fut:
                def result(self):
                    return fn(*args)

            return _Fut()

        monkeypatch.setattr(
            "asyncio.AbstractEventLoop.run_in_executor", _fake_run_in_executor
        )
        engine = EvalEngine(metrics=[TrajectoryCoherenceMetric()], parallel=True)
        traj = _make_trajectory(n_steps=4)
        result = await engine.run(traj)
        assert len(result.metric_results) == 1
        assert executor_calls == []  # inline, no thread spawn

    def test_cpu_bound_flags(self):
        from backend.eval.metrics.faithfulness import FaithfulnessMetric
        from backend.eval.metrics.context_relevance import ContextRelevanceMetric
        from backend.eval.metrics.trajectory_coherence import TrajectoryCoherenceMetric
        from backend.eval.metrics.tool_call_accuracy import ToolCallAccuracyMetric
        from backend.eval.metrics.guardrail_fp_rate import GuardrailFPRateMetric
        from backend.eval.metrics.cost_efficiency import CostEfficiencyMetric

        # Embedding/network-bound metrics stay cpu_bound=True.
        assert FaithfulnessMetric().cpu_bound is True
        assert ContextRelevanceMetric().cpu_bound is True
        # Pure-logic metrics are marked False.
        assert TrajectoryCoherenceMetric().cpu_bound is False
        assert ToolCallAccuracyMetric().cpu_bound is False
        assert GuardrailFPRateMetric().cpu_bound is False
        assert CostEfficiencyMetric().cpu_bound is False


# ---------------------------------------------------------------------------
# EvalResult model tests
# ---------------------------------------------------------------------------


class TestEvalResult:
    def test_aggregate_empty(self):
        result = EvalResult(trajectory_id="test", metric_results=[])
        assert result.aggregate_score == 0.0

    def test_aggregate_with_results(self):
        result = EvalResult(
            trajectory_id="test",
            metric_results=[
                MetricResult(metric_name="a", overall_score=0.8),
                MetricResult(metric_name="b", overall_score=0.6),
            ],
        )
        assert result.aggregate_score == pytest.approx(0.7)

    def test_scores_property(self):
        result = EvalResult(
            trajectory_id="test",
            metric_results=[
                MetricResult(metric_name="a", overall_score=0.5),
            ],
        )
        assert result.scores == {"a": 0.5}
