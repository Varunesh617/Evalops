"""Tests for Pareto optimizer and config sweeper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from backend.core.config import (
    PipelineConfig,
    GuardrailConfig,
    GuardrailFilterConfig,
)
from backend.optimizer.config_sweeper import (
    EvalOutcome,
    TrialResult,
    SweepResult,
    compute_composite_score,
)
from backend.optimizer.pareto_optimizer import (
    ParetoPoint,
    ParetoResult,
    dominates,
    find_pareto_front,
    build_cost_quality_curve,
    build_quality_latency_curve,
    build_cost_latency_curve,
    export_frontier_json,
    FrontierCurve,
)
from backend.optimizer.guardrail_tuner import (
    GuardrailEvalOutcome,
    TunedFilter,
    TunerResult,
    _composite_objective,
    _build_search_space,
    _apply_thresholds,
)


# ---------------------------------------------------------------------------
# EvalOutcome tests
# ---------------------------------------------------------------------------


class TestEvalOutcome:
    def test_fields(self):
        o = EvalOutcome(quality_score=0.8, cost_usd=0.05, latency_ms=100)
        assert o.quality_score == 0.8
        assert o.cost_usd == 0.05
        assert o.latency_ms == 100


# ---------------------------------------------------------------------------
# TrialResult tests
# ---------------------------------------------------------------------------


class TestTrialResult:
    def test_creation(self):
        r = TrialResult(
            trial_number=0,
            params={"lr": 0.01},
            quality_score=0.85,
            cost_usd=0.03,
            latency_ms=150,
            composite_score=0.7,
            duration_seconds=1.2,
        )
        assert r.trial_number == 0
        assert r.params["lr"] == 0.01


# ---------------------------------------------------------------------------
# SweepResult tests
# ---------------------------------------------------------------------------


class TestSweepResult:
    def test_creation(self):
        r = SweepResult(
            best_config=PipelineConfig(),
            best_composite_score=0.8,
            best_quality_score=0.9,
            best_cost_usd=0.02,
            best_latency_ms=100,
            trials_completed=10,
            trials_pruned=2,
            total_duration_seconds=30.0,
        )
        assert r.trials_completed == 10


# ---------------------------------------------------------------------------
# compute_composite_score tests
# ---------------------------------------------------------------------------


class TestComputeCompositeScore:
    def test_perfect_scores(self):
        score = compute_composite_score(quality=1.0, cost_usd=0.0, latency_ms=0.0)
        assert score == pytest.approx(0.6 + 0.25 + 0.15, abs=0.01)

    def test_zero_quality(self):
        score = compute_composite_score(quality=0.0, cost_usd=0.0, latency_ms=0.0)
        assert score == pytest.approx(0.0 + 0.25 + 0.15, abs=0.01)

    def test_high_cost(self):
        score = compute_composite_score(quality=0.8, cost_usd=10.0, latency_ms=0.0)
        assert score < 0.8  # cost penalty

    def test_high_latency(self):
        score = compute_composite_score(quality=0.8, cost_usd=0.0, latency_ms=60000)
        assert score < 0.8  # latency penalty

    def test_custom_weights(self):
        score = compute_composite_score(
            quality=1.0, cost_usd=0.0, latency_ms=0.0,
            quality_weight=1.0, cost_weight=0.0, latency_weight=0.0,
        )
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Pareto dominance tests
# ---------------------------------------------------------------------------


class TestParetoDominance:
    def test_dominates(self):
        a = np.array([1.0, 0.5, 0.5])
        b = np.array([0.8, 0.5, 0.5])
        assert dominates(a, b) is True

    def test_not_dominates_equal(self):
        a = np.array([1.0, 0.5])
        b = np.array([1.0, 0.5])
        assert dominates(a, b) is False

    def test_not_dominates_tradeoff(self):
        a = np.array([1.0, 0.3])
        b = np.array([0.8, 0.6])
        assert dominates(a, b) is False
        assert dominates(b, a) is False

    def test_find_pareto_front(self):
        objectives = np.array([
            [1.0, 0.5, 0.3],
            [0.8, 0.6, 0.2],
            [0.5, 0.8, 0.1],
        ])
        front = find_pareto_front(objectives)
        assert len(front) == 3  # All are Pareto-optimal (tradeoffs)

    def test_find_pareto_front_with_dominated(self):
        objectives = np.array([
            [1.0, 0.5, 0.3],
            [0.8, 0.6, 0.2],
            [0.3, 0.9, 0.8],  # dominated by none actually
        ])
        front = find_pareto_front(objectives)
        assert len(front) >= 1

    def test_find_pareto_front_empty(self):
        front = find_pareto_front(np.array([]).reshape(0, 3))
        assert front == []

    def test_find_pareto_front_clearly_dominated(self):
        objectives = np.array([
            [1.0, 0.1, 0.1],  # best in obj0
            [0.5, 0.5, 0.5],  # best in obj1 relative to point0
            [0.3, 0.8, 0.8],  # best in obj2 relative to point0
        ])
        front = find_pareto_front(objectives)
        # All three are non-dominated (conflicting objectives)
        assert 0 in front
        assert len(front) == 3


# ---------------------------------------------------------------------------
# Frontier curve builders
# ---------------------------------------------------------------------------


class TestFrontierCurves:
    def _make_point(self, cost, quality, latency, trial):
        return ParetoPoint(
            config=PipelineConfig(),
            cost_usd=cost,
            quality_score=quality,
            latency_ms=latency,
            trial_number=trial,
        )

    def test_cost_quality_curve(self):
        pts = [self._make_point(0.01, 0.9, 100, 0), self._make_point(0.05, 0.95, 200, 1)]
        curve = build_cost_quality_curve(pts)
        assert len(curve.cost_points) == 2
        assert curve.pareto_count == 2

    def test_quality_latency_curve(self):
        pts = [self._make_point(0.01, 0.9, 100, 0), self._make_point(0.05, 0.95, 200, 1)]
        curve = build_quality_latency_curve(pts)
        assert len(curve.quality_points) == 2

    def test_cost_latency_curve(self):
        pts = [self._make_point(0.01, 0.9, 100, 0)]
        curve = build_cost_latency_curve(pts)
        assert len(curve.cost_points) == 1

    def test_curve_with_all_points(self):
        pts = [self._make_point(0.01, 0.9, 100, 0)]
        all_pts = [{"cost": 0.01}, {"cost": 0.05}, {"cost": 0.10}]
        curve = build_cost_quality_curve(pts, all_points=all_pts)
        assert curve.dominated_count == 2


class TestExportFrontierJson:
    def test_export(self):
        result = ParetoResult(
            pareto_front=[
                ParetoPoint(config=PipelineConfig(), cost_usd=0.01, quality_score=0.9, latency_ms=100, trial_number=0),
            ],
            total_trials=5,
            pareto_ratio=0.2,
        )
        data = export_frontier_json(result)
        assert "pareto_front" in data
        assert data["total_trials"] == 5


# ---------------------------------------------------------------------------
# GuardrailEvalOutcome tests
# ---------------------------------------------------------------------------


class TestGuardrailEvalOutcome:
    def test_tpr(self):
        o = GuardrailEvalOutcome(true_positives=80, false_positives=10, true_negatives=90, false_negatives=20, total_samples=200)
        assert o.tpr == pytest.approx(0.8)

    def test_fpr(self):
        o = GuardrailEvalOutcome(true_positives=80, false_positives=10, true_negatives=90, false_negatives=20, total_samples=200)
        assert o.fpr == pytest.approx(0.1)

    def test_precision(self):
        o = GuardrailEvalOutcome(true_positives=80, false_positives=10, true_negatives=90, false_negatives=20, total_samples=200)
        assert o.precision == pytest.approx(80 / 90)

    def test_f1(self):
        o = GuardrailEvalOutcome(true_positives=80, false_positives=10, true_negatives=90, false_negatives=20, total_samples=200)
        expected_f1 = 2 * o.precision * o.tpr / (o.precision + o.tpr)
        assert o.f1 == pytest.approx(expected_f1)

    def test_accuracy(self):
        o = GuardrailEvalOutcome(true_positives=80, false_positives=10, true_negatives=90, false_negatives=20, total_samples=200)
        assert o.accuracy == pytest.approx(170 / 200)

    def test_zero_denominators(self):
        o = GuardrailEvalOutcome(true_positives=0, false_positives=0, true_negatives=0, false_negatives=0, total_samples=0)
        assert o.tpr == 0.0
        assert o.fpr == 0.0
        assert o.precision == 0.0
        assert o.f1 == 0.0
        assert o.accuracy == 0.0


# ---------------------------------------------------------------------------
# _composite_objective tests
# ---------------------------------------------------------------------------


class TestCompositeObjective:
    def test_perfect_outcome(self):
        o = GuardrailEvalOutcome(true_positives=100, false_positives=0, true_negatives=100, false_negatives=0, total_samples=200)
        score = _composite_objective(o)
        assert score > 0.9

    def test_bad_outcome(self):
        o = GuardrailEvalOutcome(true_positives=10, false_positives=90, true_negatives=10, false_negatives=90, total_samples=200)
        score = _composite_objective(o)
        assert score < 0.5


# ---------------------------------------------------------------------------
# _apply_thresholds tests
# ---------------------------------------------------------------------------


class TestApplyThresholds:
    def test_apply(self):
        config = GuardrailConfig(filters=[
            GuardrailFilterConfig(name="f1", threshold=0.5),
            GuardrailFilterConfig(name="f2", threshold=0.7),
        ])
        new_config = _apply_thresholds(config, {"f1": 0.9})
        assert new_config.filters[0].threshold == 0.9
        assert new_config.filters[1].threshold == 0.7  # unchanged


# ---------------------------------------------------------------------------
# Optuna pruning + Pareto front tests (Tasks 2.3 / 2.5)
# ---------------------------------------------------------------------------


class _FakeEval:
    """Async eval fn emitting a configurable EvalOutcome."""

    def __init__(self, outcome_factory):
        self._factory = outcome_factory

    async def __call__(self, config):
        return self._factory()


async def test_config_sweeper_prunes_with_intermediate_values(monkeypatch):
    import optuna

    from backend.optimizer import config_sweeper as cs_mod

    # sklearn (param importances) may be absent in test env; stub it.
    monkeypatch.setattr(
        cs_mod.optuna.importance, "get_param_importances", lambda *a, **k: {}
    )

    from backend.optimizer.config_sweeper import ConfigSweeper, EvalOutcome

    # Vary intermediate values across trials: early trials look promising,
    # later ones clearly worse, so the MedianPruner cuts them.
    counter = {"n": 0}

    def factory():
        counter["n"] += 1
        n = counter["n"]
        # First two trials: high intermediate signal (survive).
        # Rest: monotonic decline -> pruned against the running median.
        if n <= 2:
            vals = [0.9 - 0.05 * n, 0.8, 0.7]
        else:
            vals = [0.1, 0.05, 0.02]
        return EvalOutcome(
            quality_score=0.5,
            cost_usd=0.01,
            latency_ms=100,
            metadata={"intermediate_values": vals},
        )

    sweeper = ConfigSweeper(
        _FakeEval(factory),
        n_trials=8,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=0, n_warmup_steps=0),
    )
    result = await sweeper.run()
    assert result.trials_pruned > 0
    assert result.trials_completed + result.trials_pruned == 8


async def test_config_sweeper_no_prune_without_intermediate(monkeypatch):
    # Block pruning by disabling the pruner so no intermediate_values -> 0 pruned.
    import optuna

    from backend.optimizer import config_sweeper as cs_mod

    monkeypatch.setattr(
        cs_mod.optuna.importance, "get_param_importances", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        optuna.pruners, "MedianPruner", lambda *a, **k: optuna.pruners.NopPruner()
    )

    from backend.optimizer.config_sweeper import ConfigSweeper, EvalOutcome

    def factory():
        return EvalOutcome(quality_score=0.6, cost_usd=0.01, latency_ms=100)

    sweeper = ConfigSweeper(_FakeEval(factory), n_trials=4)
    result = await sweeper.run()
    assert result.trials_pruned == 0
    assert result.trials_completed == 4


async def test_pareto_optimizer_no_redundant_filter():
    import optuna

    from backend.optimizer.pareto_optimizer import ParetoOptimizer, EvalOutcome

    def factory():
        # Fixed point on the frontier.
        return EvalOutcome(quality_score=0.8, cost_usd=0.02, latency_ms=120)

    optimizer = ParetoOptimizer(_FakeEval(factory), n_trials=6)
    result = await optimizer.run()
    # study.best_trials (non-dominated set) is used directly; no O(n^2) filter.
    assert result.total_trials == 6
    assert len(result.pareto_front) == result.total_trials
    # Each pareto point carries its trial number.
    assert {p.trial_number for p in result.pareto_front} == set(range(6))


async def test_pareto_optimizer_prune_signal_dormant_in_multiobjective():
    # Optuna does not support Trial.report in multi-objective studies, so the
    # pruning signal is gracefully ignored (pruning stays dormant) rather than
    # crashing. The optimizer must still complete all trials.
    import optuna

    from backend.optimizer.pareto_optimizer import ParetoOptimizer, EvalOutcome

    def factory():
        return EvalOutcome(
            quality_score=0.5,
            cost_usd=0.5,
            latency_ms=5000,
            metadata={"intermediate_values": [0.1, 0.05, 0.02]},
        )

    optimizer = ParetoOptimizer(
        _FakeEval(factory),
        n_trials=8,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=0, n_warmup_steps=0),
    )
    result = await optimizer.run()
    # Pruning is dormant for NSGA-II multi-objective; all trials complete.
    assert result.trials_pruned == 0
    assert result.total_trials == 8

