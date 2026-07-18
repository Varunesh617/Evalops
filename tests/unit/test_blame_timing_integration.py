"""End-to-end integration test for blame attribution + step timing.

Verifies the SLOW_STEP failure mode and step_timings propagation added to
``backend.eval.blame_attribution``.  Builds a :class:`Trajectory` directly
via ``backend.core.tracer`` (no live pipeline required).

Run after the blame-attribution agent changes land:

    python -m pytest tests/unit/test_blame_timing_integration.py -q
"""

from __future__ import annotations

from backend.core.config import StepStatus
from backend.core.tracer import TokenUsage, Trajectory, TrajectoryStep
from backend.eval.blame_attribution import BlameAttributionEngine, FailureMode


def _make_generate_step(latency_ms: float, tokens: TokenUsage) -> TrajectoryStep:
    return TrajectoryStep(
        step_name="generate",
        status=StepStatus.SUCCESS,
        latency_ms=latency_ms,
        tokens=tokens,
    )


class TestBlameTimingIntegration:
    def test_slow_step_detected_and_timings_propagated(self):
        trajectory = Trajectory(run_id="slow-run")
        trajectory.add_step(
            TrajectoryStep(
                step_name="retrieve",
                status=StepStatus.SUCCESS,
                latency_ms=120,
                tokens=TokenUsage(prompt_tokens=10, completion_tokens=0, total_tokens=10),
            )
        )
        trajectory.add_step(
            _make_generate_step(
                latency_ms=120000.0,
                tokens=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
            )
        )

        report = BlameAttributionEngine().analyse(trajectory)

        # Root cause should point at the slow generate step / SLOW_STEP mode.
        assert report.root_cause_step == "generate" or report.root_cause_mode == FailureMode.SLOW_STEP
        assert FailureMode.SLOW_STEP in (report.root_cause_mode,)

        # step_timings must be populated and contain the slow generate entry.
        assert report.step_timings, "step_timings should be non-empty"
        gen_entry = next(e for e in report.step_timings if e["step"] == "generate")
        assert gen_entry["latency_ms"] == 120000
        assert gen_entry["total_tokens"] == 300

        # to_dict must surface step_timings.
        d = report.to_dict()
        assert "step_timings" in d
        assert d["step_timings"] == report.step_timings

    def test_healthy_trajectory_scores_one(self):
        trajectory = Trajectory(run_id="healthy-run")
        for name in ("retrieve", "rerank", "generate"):
            trajectory.add_step(
                TrajectoryStep(
                    step_name=name,
                    status=StepStatus.SUCCESS,
                    latency_ms=50.0,
                    tokens=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                )
            )

        report = BlameAttributionEngine().analyse(trajectory)

        assert report.score == 1.0
        assert report.root_cause_step == "none"
