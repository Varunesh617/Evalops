"""Tests for the blame attribution engine's slow-step and timing features."""

from __future__ import annotations

from backend.core.config import PipelineConfig, StepStatus
from backend.core.tracer import TokenUsage, Tracer, Trajectory, TrajectoryStep
from backend.eval.blame_attribution import (
    BlameAttributionEngine,
    FailureMode,
    DEFAULT_STEP_LATENCY_BUDGET_MS,
)


def _make_trajectory(steps: list[tuple[str, StepStatus, float | None, int]]) -> Trajectory:
    tracer = Tracer(sample_rate=0.0)
    traj = tracer.start(pipeline_id="test")
    for name, status, latency, tokens in steps:
        step = TrajectoryStep(step_name=name, status=status)
        if latency is not None:
            step.latency_ms = latency
        step.tokens = TokenUsage(total_tokens=tokens)
        traj.steps.append(step)
    tracer.finish(traj)
    return traj


def test_slow_generate_step_triggers_slow_step() -> None:
    budget = DEFAULT_STEP_LATENCY_BUDGET_MS["generate"]
    traj = _make_trajectory([("generate", StepStatus.SUCCESS, budget + 1000.0, 10)])
    report = BlameAttributionEngine().analyse(traj)
    assert report.root_cause_mode == FailureMode.SLOW_STEP
    assert "latency budget" in report.root_cause_message.lower()


def test_step_timings_populated() -> None:
    traj = _make_trajectory(
        [
            ("retrieve", StepStatus.SUCCESS, 120.0, 5),
            ("generate", StepStatus.SUCCESS, 8000.0, 25),
        ]
    )
    report = BlameAttributionEngine().analyse(traj)
    assert len(report.step_timings) == 2
    assert report.step_timings[0]["step"] == "retrieve"
    assert report.step_timings[0]["latency_ms"] == 120.0
    assert report.step_timings[0]["total_tokens"] == 5
    assert report.step_timings[1]["total_tokens"] == 25
    assert report.to_dict()["step_timings"] == report.step_timings
