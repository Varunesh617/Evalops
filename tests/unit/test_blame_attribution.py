"""Tests for blame attribution engine in backend.eval.blame_attribution."""

from __future__ import annotations

import pytest

from backend.core.config import StepStatus
from backend.core.tracer import TokenUsage, Trajectory, TrajectoryStep
from backend.eval.blame_attribution import (
    BlameAttributionEngine,
    BlameReport,
    CascadeLink,
    FailureMode,
    Severity,
)


# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


class TestFailureMode:
    def test_all_modes(self):
        modes = set(FailureMode)
        expected = {
            "timeout", "low_score", "guardrail_violation",
            "empty_result", "token_limit", "exception",
            "degradation", "unknown",
        }
        assert {m.value for m in modes} == expected


class TestSeverity:
    def test_ordering(self):
        vals = [s.value for s in Severity]
        assert vals == ["low", "medium", "high", "critical"]


class TestBlameReport:
    def test_to_dict(self):
        report = BlameReport(
            run_id="test-run",
            root_cause_step="retrieve",
            root_cause_mode=FailureMode.TIMEOUT,
            root_cause_message="Timed out",
            severity=Severity.CRITICAL,
            cascade_chain=[
                CascadeLink(
                    step_name="retrieve",
                    failure_mode=FailureMode.TIMEOUT,
                    severity=Severity.CRITICAL,
                    message="Step timed out",
                    propagated=False,
                ),
            ],
            remediation=["Increase timeout"],
            score=0.3,
        )
        d = report.to_dict()
        assert d["run_id"] == "test-run"
        assert d["root_cause_mode"] == "timeout"
        assert len(d["cascade_chain"]) == 1
        assert d["cascade_chain"][0]["propagated"] is False


# ---------------------------------------------------------------------------
# Engine analysis tests
# ---------------------------------------------------------------------------


class TestBlameAttributionEngine:
    def setup_method(self):
        self.engine = BlameAttributionEngine()

    def test_all_steps_succeed(self, successful_trajectory):
        report = self.engine.analyse(successful_trajectory)
        assert report.root_cause_step == "none"
        assert report.root_cause_mode == FailureMode.UNKNOWN
        assert report.severity == Severity.LOW
        assert report.score == 1.0

    def test_empty_trajectory(self):
        traj = Trajectory(pipeline_id="test")
        report = self.engine.analyse(traj)
        assert report.root_cause_step == "none"
        assert report.score == 1.0

    def test_exception_failure(self, failing_trajectory):
        report = self.engine.analyse(failing_trajectory)
        assert report.root_cause_step == "rerank"
        assert report.root_cause_mode == FailureMode.EXCEPTION
        assert report.severity == Severity.HIGH
        assert report.score < 1.0
        assert len(report.cascade_chain) > 0
        assert len(report.remediation) > 0

    def test_timeout_failure(self):
        traj = Trajectory(pipeline_id="test")
        step = TrajectoryStep(
            step_name="retrieve",
            status=StepStatus.TIMED_OUT,
        )
        step.finish(status=StepStatus.TIMED_OUT)
        traj.add_step(step)

        # Add successful step after
        step2 = TrajectoryStep(step_name="rerank", status=StepStatus.SUCCESS)
        step2.finish(status=StepStatus.SUCCESS)
        traj.add_step(step2)

        report = self.engine.analyse(traj)
        assert report.root_cause_mode == FailureMode.TIMEOUT
        assert report.severity == Severity.CRITICAL
        assert any("timeout" in r.lower() for r in report.remediation)

    def test_low_score_failure(self):
        traj = Trajectory(pipeline_id="test")
        step = TrajectoryStep(step_name="reason", status=StepStatus.SUCCESS)
        step.metrics.score = 0.2
        step.finish(status=StepStatus.SUCCESS)
        traj.add_step(step)

        step.status = StepStatus.FAILED
        report = self.engine.analyse(traj)
        assert report.root_cause_mode == FailureMode.LOW_SCORE
        assert report.severity == Severity.MEDIUM

    def test_empty_result_retrieve(self):
        traj = Trajectory(pipeline_id="test")
        step = TrajectoryStep(step_name="retrieve", status=StepStatus.SUCCESS)
        step.payload["result"] = {"count": 0}
        step.status = StepStatus.FAILED  # Force failure
        step.finish(status=StepStatus.FAILED)
        traj.add_step(step)

        report = self.engine.analyse(traj)
        # empty_result is more specific than exception, fires first with reordered rules
        assert report.root_cause_mode == FailureMode.EMPTY_RESULT

    def test_guardrail_violation(self):
        traj = Trajectory(pipeline_id="test")
        step = TrajectoryStep(step_name="guardrail", status=StepStatus.SUCCESS)
        step.payload["result"] = {"passed": False, "violations": ["toxicity"]}
        step.status = StepStatus.FAILED
        step.finish(status=StepStatus.FAILED)
        traj.add_step(step)

        report = self.engine.analyse(traj)
        assert report.root_cause_mode == FailureMode.GUARDRAIL_VIOLATION

    def test_token_limit_failure(self):
        traj = Trajectory(pipeline_id="test")
        step = TrajectoryStep(
            step_name="reason",
            status=StepStatus.FAILED,
            error="context_length exceeded",
        )
        step.finish(status=StepStatus.FAILED, error="context_length exceeded")
        traj.add_step(step)

        report = self.engine.analyse(traj)
        assert report.root_cause_mode == FailureMode.TOKEN_LIMIT
        assert "Token limit exceeded" in report.root_cause_message

    def test_counterfactuals_generated(self, failing_trajectory):
        report = self.engine.analyse(failing_trajectory)
        assert len(report.counterfactuals) > 0
        for cf in report.counterfactuals:
            assert "hypothetical_step" in cf
            assert "assumption" in cf

    def test_rubric_generated(self, failing_trajectory):
        report = self.engine.analyse(failing_trajectory)
        assert "rubric_version" in report.rubric
        assert "dimensions" in report.rubric
        assert len(report.rubric["dimensions"]) == 5

    def test_llm_judgement_disabled_is_empty(self, failing_trajectory, monkeypatch):
        # Default: LLM disabled -> no network, llm_judgement == {}.
        monkeypatch.setenv("EVALOPS_LLM_ENABLED", "false")
        report = self.engine.analyse(failing_trajectory)
        assert report.llm_judgement == {}
        assert report.to_dict()["llm_judgement"] == {}

    def test_llm_judgement_enabled_success(self, failing_trajectory, monkeypatch):
        monkeypatch.setenv("EVALOPS_LLM_ENABLED", "true")

        class _FakeClient:
            enabled = True

            def judge(self, rubric):
                return {
                    "scores": {"retrieval_quality": 0.7},
                    "rationale": "looks fine",
                }

        monkeypatch.setattr(
            "backend.eval.blame_attribution.LLMJudgeClient",
            lambda: _FakeClient(),
        )
        report = self.engine.analyse(failing_trajectory)
        assert report.llm_judgement["scores"] == {"retrieval_quality": 0.7}
        assert "rationale" in report.llm_judgement
        assert "llm_judgement" in report.to_dict()

    def test_llm_judgement_graceful_on_error(self, failing_trajectory, monkeypatch):
        monkeypatch.setenv("EVALOPS_LLM_ENABLED", "true")

        import backend.eval.llm_judge as lj

        class _BoomClient:
            enabled = True

            def judge(self, rubric):
                raise lj.LLMJudgeError("boom")

        monkeypatch.setattr(
            "backend.eval.blame_attribution.LLMJudgeClient",
            lambda: _BoomClient(),
        )
        # Must still return a valid report.
        report = self.engine.analyse(failing_trajectory)
        assert report.llm_judgement == {}
        assert report.root_cause_step != "none"

    def test_score_penalises_failures(self, failing_trajectory):
        report = self.engine.analyse(failing_trajectory)
        assert 0.0 <= report.score <= 1.0
        assert report.score < 1.0

    def test_remediation_suggestions_per_mode(self):
        for mode, expected_phrase in [
            (FailureMode.TIMEOUT, "Increase timeout"),
            (FailureMode.LOW_SCORE, "Review input quality"),
            (FailureMode.EMPTY_RESULT, "Check that the upstream"),
            (FailureMode.TOKEN_LIMIT, "Reduce top_k"),
            (FailureMode.EXCEPTION, "Inspect the stack trace"),
            (FailureMode.GUARDRAIL_VIOLATION, "adjusting guardrail"),
            (FailureMode.DEGRADATION, "Investigate why"),
            (FailureMode.UNKNOWN, "Review logs"),
        ]:
            suggestions = BlameAttributionEngine._suggest_remediation(
                TrajectoryStep(step_name="test"), mode
            )
            assert any(expected_phrase in s for s in suggestions), f"Missing for {mode}"

    def test_cascade_with_success_recovery(self):
        traj = Trajectory(pipeline_id="test")

        # Failing step
        s1 = TrajectoryStep(step_name="retrieve", status=StepStatus.FAILED)
        s1.finish(status=StepStatus.FAILED, error="err")
        traj.add_step(s1)

        # Successful recovery step
        s2 = TrajectoryStep(step_name="rerank", status=StepStatus.SUCCESS)
        s2.finish(status=StepStatus.SUCCESS)
        traj.add_step(s2)

        # Another failure
        s3 = TrajectoryStep(step_name="reason", status=StepStatus.FAILED)
        s3.finish(status=StepStatus.FAILED, error="err2")
        traj.add_step(s3)

        report = self.engine.analyse(traj)
        # Should have a recovery link
        recovery_links = [c for c in report.cascade_chain if c.failure_mode == FailureMode.DEGRADATION]
        assert len(recovery_links) > 0
