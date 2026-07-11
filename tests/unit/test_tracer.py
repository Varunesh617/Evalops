"""Tests for the trajectory tracer in backend.core.tracer."""

from __future__ import annotations

import time

import pytest

from backend.core.config import StepStatus
from backend.core.tracer import (
    TokenUsage,
    Trajectory,
    TrajectoryStep,
    Tracer,
)


# ---------------------------------------------------------------------------
# TokenUsage tests
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_defaults(self):
        tu = TokenUsage()
        assert tu.prompt_tokens == 0
        assert tu.completion_tokens == 0
        assert tu.total_tokens == 0

    def test_custom_values(self):
        tu = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert tu.prompt_tokens == 10

    def test_addition(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        b = TokenUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15)
        c = a + b
        assert c.prompt_tokens == 15
        assert c.completion_tokens == 30
        assert c.total_tokens == 45

    def test_addition_with_zero(self):
        a = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        b = TokenUsage()
        c = a + b
        assert c == a

    def test_frozen(self):
        tu = TokenUsage(prompt_tokens=1)
        with pytest.raises(AttributeError):
            tu.prompt_tokens = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# StepMetrics tests
# ---------------------------------------------------------------------------


class TestStepMetrics:
    def test_defaults(self):
        from backend.core.tracer import StepMetrics

        sm = StepMetrics()
        assert sm.score is None
        assert sm.confidence is None
        assert sm.metadata == {}

    def test_custom(self):
        from backend.core.tracer import StepMetrics

        sm = StepMetrics(score=0.95, confidence=0.8, metadata={"key": "value"})
        assert sm.score == 0.95
        assert sm.metadata["key"] == "value"


# ---------------------------------------------------------------------------
# TrajectoryStep tests
# ---------------------------------------------------------------------------


class TestTrajectoryStep:
    def test_defaults(self):
        step = TrajectoryStep()
        assert step.step_id is not None
        assert len(step.step_id) == 12
        assert step.step_name == ""
        assert step.status == StepStatus.PENDING
        assert step.end_time is None
        assert step.latency_ms is None
        assert step.error is None

    def test_finish_success(self):
        step = TrajectoryStep(step_name="retrieve")
        step.status = StepStatus.RUNNING
        time.sleep(0.01)
        step.finish(status=StepStatus.SUCCESS)
        assert step.status == StepStatus.SUCCESS
        assert step.end_time is not None
        assert step.latency_ms is not None
        assert step.latency_ms >= 0

    def test_finish_with_error(self):
        step = TrajectoryStep(step_name="fail_step")
        step.finish(
            status=StepStatus.FAILED,
            error="something broke",
            error_type="RuntimeError",
        )
        assert step.status == StepStatus.FAILED
        assert step.error == "something broke"
        assert step.error_type == "RuntimeError"

    def test_finish_with_span_mock(self):
        span_mock = type("Span", (), {"set_status": lambda self, s: None, "end": lambda self: None})()
        step = TrajectoryStep(step_name="otel_step", span=span_mock)
        step.finish(status=StepStatus.SUCCESS)
        assert step.status == StepStatus.SUCCESS

    def test_to_dict(self):
        step = TrajectoryStep(step_name="test_step")
        step.finish(status=StepStatus.SUCCESS)
        d = step.to_dict()
        assert d["step_name"] == "test_step"
        assert d["status"] == "success"
        assert "tokens" in d
        assert "metrics" in d
        assert d["error"] is None
        assert d["payload_keys"] == []


# ---------------------------------------------------------------------------
# Trajectory tests
# ---------------------------------------------------------------------------


class TestTrajectory:
    def test_defaults(self):
        traj = Trajectory()
        assert traj.run_id is not None
        assert len(traj.run_id) == 32  # hex uuid
        assert traj.steps == []
        assert traj.overall_score is None

    def test_add_step_accumulates_tokens(self):
        traj = Trajectory()
        step = TrajectoryStep(
            step_name="retrieve",
            tokens=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        traj.add_step(step)
        assert len(traj.steps) == 1
        assert traj.total_tokens.total_tokens == 150

    def test_add_multiple_steps(self):
        traj = Trajectory()
        for i in range(5):
            step = TrajectoryStep(
                step_name=f"step_{i}",
                tokens=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
            traj.add_step(step)
        assert traj.total_tokens.total_tokens == 75

    def test_finalise(self):
        traj = Trajectory()
        traj.finalise()
        assert traj.end_time is not None

    def test_latency_ms_before_finalise(self):
        traj = Trajectory()
        assert traj.latency_ms is None

    def test_latency_ms_after_finalise(self):
        traj = Trajectory()
        time.sleep(0.01)
        traj.finalise()
        assert traj.latency_ms is not None
        assert traj.latency_ms >= 0

    def test_failed_steps(self):
        traj = Trajectory()
        s1 = TrajectoryStep(step_name="ok", status=StepStatus.SUCCESS)
        s2 = TrajectoryStep(step_name="fail", status=StepStatus.FAILED)
        traj.add_step(s1)
        traj.add_step(s2)
        assert len(traj.failed_steps) == 1
        assert traj.failed_steps[0].step_name == "fail"

    def test_succeeded_all_success(self):
        traj = Trajectory()
        traj.add_step(TrajectoryStep(status=StepStatus.SUCCESS))
        assert traj.succeeded is True

    def test_succeeded_with_failure(self):
        traj = Trajectory()
        traj.add_step(TrajectoryStep(status=StepStatus.SUCCESS))
        traj.add_step(TrajectoryStep(status=StepStatus.FAILED))
        assert traj.succeeded is False

    def test_to_dict(self, successful_trajectory):
        d = successful_trajectory.to_dict()
        assert d["pipeline_id"] == "test-pipeline"
        assert len(d["steps"]) == 5
        assert d["total_tokens"]["total_tokens"] == 750  # 150 * 5

    def test_to_dict_empty(self):
        traj = Trajectory(pipeline_id="empty")
        d = traj.to_dict()
        assert d["steps"] == []
        assert d["end_time"] is None
        assert d["latency_ms"] is None


# ---------------------------------------------------------------------------
# Tracer tests
# ---------------------------------------------------------------------------


class TestTracer:
    def test_init(self):
        tracer = Tracer()
        assert tracer._sample_rate == 1.0

    def test_start_creates_trajectory(self):
        tracer = Tracer(sample_rate=0.0)
        traj = tracer.start(pipeline_id="my-pipeline")
        assert isinstance(traj, Trajectory)
        assert traj.pipeline_id == "my-pipeline"

    def test_should_sample_disabled(self):
        tracer = Tracer(sample_rate=0.0)
        assert tracer._should_sample() is False

    def test_should_sample_enabled(self):
        tracer = Tracer(sample_rate=1.0)
        assert tracer._should_sample() is True

    @pytest.mark.asyncio
    async def test_step_context_manager_success(self):
        tracer = Tracer(sample_rate=0.0)
        traj = tracer.start("test")

        async with tracer.step(traj, "my_step") as step:
            step.payload["result"] = {"value": 42}
            step.tokens = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        assert len(traj.steps) == 1
        assert traj.steps[0].status == StepStatus.SUCCESS
        assert traj.steps[0].payload["result"]["value"] == 42

    @pytest.mark.asyncio
    async def test_step_context_manager_exception(self):
        tracer = Tracer(sample_rate=0.0)
        traj = tracer.start("test")

        with pytest.raises(ValueError, match="boom"):
            async with tracer.step(traj, "failing_step"):
                raise ValueError("boom")

        assert len(traj.steps) == 1
        assert traj.steps[0].status == StepStatus.FAILED
        assert traj.steps[0].error == "boom"
        assert traj.steps[0].error_type == "ValueError"

    @pytest.mark.asyncio
    async def test_step_manual_status_preserved(self):
        tracer = Tracer(sample_rate=0.0)
        traj = tracer.start("test")

        async with tracer.step(traj, "manual") as step:
            step.finish(status=StepStatus.SKIPPED)

        assert traj.steps[0].status == StepStatus.SKIPPED

    def test_finish(self):
        tracer = Tracer(sample_rate=0.0)
        traj = tracer.start("test")
        result = tracer.finish(traj)
        assert result is traj
        assert traj.end_time is not None
