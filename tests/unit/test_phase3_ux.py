"""Phase 3 UX tests — counterfactual real-run, recommendation feedback,
cost/latency tradeoff, personalized smart defaults, shared trace loading,
historical pagination.

No DATABASE_URL is required: the applied-recommendation store falls back to an
in-memory dict and the counterfactual engine falls back to simulation.
"""

from __future__ import annotations

import pytest

from backend.core.config import StepStatus
from backend.core.tracer import StepMetrics, TokenUsage, Trajectory, TrajectoryStep
from backend.db.models import Base
from backend.db.repository import AppliedRecommendationRepository
from backend.db.session import async_sessionmaker, create_async_engine
from backend.diagnosis.counterfactual import (
    ChangeType,
    CounterfactualEngine,
    Intervention,
    PipelineExecutor,
    VariantResult,
)
from backend.diagnosis.recommender import RecommendationEngine
from backend.eval.blame_attribution import BlameAttributionEngine
from backend.tuning.smart_defaults import PipelineUsageStats, SmartDefaults
from backend.tuning.user_preferences import (
    DomainType,
    FilterPreference,
    MetricPreference,
    UserPreferences,
)


def _failing_trajectory() -> Trajectory:
    traj = Trajectory(pipeline_id="p1", run_id="t1")
    s1 = TrajectoryStep(step_name="retrieve", status=StepStatus.SUCCESS)
    s1.finish(status=StepStatus.SUCCESS)
    traj.add_step(s1)
    s2 = TrajectoryStep(
        step_name="rerank",
        status=StepStatus.FAILED,
        error="timeout",
        metrics=StepMetrics(score=0.2),
    )
    s2.finish(status=StepStatus.FAILED, error="timeout")
    traj.add_step(s2)
    traj.finalise()
    return traj


class _RecordingExecutor(PipelineExecutor):
    """A fake executor that returns a fixed variant score (never mutates)."""

    def __init__(self, score: float, cost: float = 0.01, latency: float = 400.0) -> None:
        self.score = score
        self.cost = cost
        self.latency = latency
        self.calls: list[Intervention] = []

    async def run_variant(
        self, trace: Trajectory, intervention: Intervention, *, timeout_seconds: float = 60.0
    ) -> VariantResult:
        # Prove the original trace is untouched.
        assert trace.steps[1].step_name == "rerank"
        self.calls.append(intervention)
        return VariantResult(
            overall_score=self.score,
            step_scores={"retrieve": 0.9, "rerank": self.score},
            cost_usd=self.cost,
            latency_ms=self.latency,
        )


# ---------------------------------------------------------------------------
# 3.1 — real re-run mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_real_uses_executor_and_reports_delta():
    traj = _failing_trajectory()
    blame = BlameAttributionEngine().analyse(traj)
    engine = CounterfactualEngine(blame_engine=BlameAttributionEngine())
    executor = _RecordingExecutor(score=0.85, cost=0.02, latency=350.0)
    engine.set_executor(executor)

    iv = Intervention(
        ChangeType.REASONING_MODEL,
        original_value="gpt-4o",
        counterfactual_value="gpt-4o-mini",
        description="faster model",
    )
    result = await engine.run_real(traj, iv, blame=blame, timeout_seconds=5.0)

    assert executor.calls == [iv]
    assert result.counterfactual_score == pytest.approx(0.85, abs=0.01)
    assert result.improvement_delta > 0
    assert result.cost_usd == 0.02
    assert result.latency_ms == 350.0
    # Original trace must be untouched.
    assert traj.steps[1].status == StepStatus.FAILED


@pytest.mark.anyio
async def test_run_real_falls_back_to_simulation_without_executor():
    traj = _failing_trajectory()
    blame = BlameAttributionEngine().analyse(traj)
    engine = CounterfactualEngine(blame_engine=BlameAttributionEngine())
    iv = Intervention(
        ChangeType.RETRIEVAL_TOP_K,
        original_value=20,
        counterfactual_value=50,
        description="more docs",
    )
    result = await engine.run_real(traj, iv, blame=blame)
    assert result.error is None
    assert isinstance(result.counterfactual_score, float)


@pytest.mark.anyio
async def test_run_real_handles_executor_error_gracefully():
    traj = _failing_trajectory()
    blame = BlameAttributionEngine().analyse(traj)

    class _BoomExecutor(PipelineExecutor):
        async def run_variant(self, trace, intervention, *, timeout_seconds=60.0):
            raise RuntimeError("boom")

    engine = CounterfactualEngine(blame_engine=BlameAttributionEngine())
    engine.set_executor(_BoomExecutor())
    iv = Intervention(
        ChangeType.REASONING_MODEL, "a", "b", "x"
    )
    result = await engine.run_real(traj, iv, blame=blame)
    assert result.error is not None
    assert result.improvement_delta == 0.0


# ---------------------------------------------------------------------------
# 3.5 — cost / latency tradeoff on recommendations
# ---------------------------------------------------------------------------


def test_recommendations_include_cost_latency_deltas():
    # Use a reason-failing trajectory so linked (reasoning_model etc.) rules fire.
    traj = Trajectory(pipeline_id="p1", run_id="t2")
    s1 = TrajectoryStep(step_name="retrieve", status=StepStatus.SUCCESS)
    s1.finish(status=StepStatus.SUCCESS)
    traj.add_step(s1)
    s2 = TrajectoryStep(
        step_name="reason",
        status=StepStatus.FAILED,
        error="low score",
        metrics=StepMetrics(score=0.2),
    )
    s2.finish(status=StepStatus.FAILED, error="low score")
    traj.add_step(s2)
    traj.finalise()

    blame = BlameAttributionEngine().analyse(traj)
    recs = RecommendationEngine().recommend(blame)
    # At least one recommendation should carry a linked change_type + estimate.
    linked = [r for r in recs.recommendations if r.change_type is not None]
    assert linked, "expected some recommendations linked to a change type"
    for r in linked:
        assert r.estimated_cost_delta_usd is not None
        assert r.estimated_latency_delta_ms is not None
        assert "estimated_cost_delta_usd" in r.to_dict()


# ---------------------------------------------------------------------------
# 3.3 — personalized smart defaults
# ---------------------------------------------------------------------------


def test_smart_defaults_personalized_from_user_prefs():
    prefs = UserPreferences(
        user_id="bob",
        domain=DomainType.HEALTHCARE,
        metrics=[
            MetricPreference(name="faithfulness", enabled=True, weight=3.0),
            MetricPreference(name="cost_efficiency", enabled=True, weight=2.0),
        ],
        filters=[FilterPreference(name="pii", enabled=True, threshold=0.4, priority=90)],
    )
    stats = PipelineUsageStats(domain=DomainType.HEALTHCARE, avg_cost_usd=0.5)
    result = SmartDefaults(stats, user_preferences=prefs).generate()
    assert result.domain == DomainType.HEALTHCARE
    assert "Personalised" in result.reasoning
    names = {m.name for m in result.metric_suggestions}
    assert "faithfulness" in names


def test_smart_defaults_domain_defaults_without_user_prefs():
    stats = PipelineUsageStats(domain=DomainType.GENERAL)
    result = SmartDefaults(stats).generate()
    assert result.domain == DomainType.GENERAL
    assert not any("Personalised" in r for r in [result.reasoning])


# ---------------------------------------------------------------------------
# 3.4 / 3.6 — shared trace loading + historical pagination (route level)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_load_failure_records_helper():
    from backend.api.routes.diagnosis import _load_failure_records

    traj = _failing_trajectory()

    class _FakeRepo:
        def __init__(self, items):
            self._items = items

        async def list(self, *, status=None, page=1, page_size=100):
            start = (page - 1) * page_size
            chunk = self._items[start : start + page_size]
            return chunk, len(self._items)

    fake_record = {
        "id": "t1",
        "pipeline_id": "p1",
        "status": "failed",
        "started_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        "steps": [
            {
                "step_name": "retrieve",
                "status": "success",
                "metrics": {"score": 0.9},
                "tokens": {"total_tokens": 10},
            },
            {
                "step_name": "rerank",
                "status": "failed",
                "error": "timeout",
                "metrics": {"score": 0.2},
                "tokens": {"total_tokens": 10},
            },
        ],
        "metadata": {"model": "gpt-4o"},
    }
    repo = _FakeRepo([fake_record])
    records = await _load_failure_records(repo, BlameAttributionEngine(), page_size=10)
    assert len(records) == 1
    assert records[0].root_cause_step == "rerank"
    assert records[0].model == "gpt-4o"


# ---------------------------------------------------------------------------
# 3.2 — recommendation feedback (in-memory) + DB persistence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_applied_recommendation_in_memory_roundtrip():
    import backend.api.routes.diagnosis as diag

    diag._applied_recommendation_repo = None
    diag._applied_store.clear()

    from backend.api.routes.diagnosis import (
        _list_applied_for_user,
        _persist_applied_recommendation,
        _update_applied_outcome,
    )

    rec = {
        "id": "applied-test",
        "user_id": "carol",
        "trace_id": "t1",
        "recommendation_id": "rec-1",
        "category": "retrieval",
        "action": "increase top_k",
        "change_type": "retrieval_top_k",
        "applied_at": "2026-01-01T00:00:00+00:00",
        "outcome_status": "pending",
        "measured_delta": None,
        "measured_cost_delta": None,
        "measured_latency_delta_ms": None,
        "outcome_notes": "",
        "metadata": {},
    }
    await _persist_applied_recommendation(rec)
    items, total = await _list_applied_for_user("carol")
    assert total == 1
    assert items[0]["recommendation_id"] == "rec-1"

    updated = await _update_applied_outcome(
        "rec-1",
        outcome_status="success",
        measured_delta=0.15,
        measured_cost_delta=0.003,
        measured_latency_delta_ms=120.0,
        outcome_notes="helped",
    )
    assert updated is not None
    assert updated["outcome_status"] == "success"
    assert updated["measured_delta"] == 0.15

    missing = await _update_applied_outcome(
        "nope",
        outcome_status="success",
        measured_delta=None,
        measured_cost_delta=None,
        measured_latency_delta_ms=None,
        outcome_notes="",
    )
    assert missing is None


@pytest.mark.anyio
async def test_applied_recommendation_db_persistence():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)

    async with factory() as session:
        repo = AppliedRecommendationRepository(session)
        await repo.create(
            {
                "id": "applied-db",
                "user_id": "dave",
                "trace_id": "t2",
                "recommendation_id": "rec-db",
                "category": "reasoning",
                "action": "switch model",
                "change_type": "reasoning_model",
                "outcome_status": "pending",
            }
        )
        items, total = await repo.list_for_user("dave")
        assert total == 1
        assert items[0]["recommendation_id"] == "rec-db"

        updated = await repo.update_outcome(
            "rec-db",
            outcome_status="success",
            measured_delta=0.2,
            measured_cost_delta=0.01,
            measured_latency_delta_ms=-300.0,
        )
        assert updated is not None
        assert updated["measured_delta"] == 0.2

        none = await repo.update_outcome("missing", outcome_status="fail")
        assert none is None

    await eng.dispose()
