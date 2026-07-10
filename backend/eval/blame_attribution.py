"""Blame attribution engine — root cause analysis for pipeline failures.

Analyses a completed :class:`~backend.core.tracer.Trajectory` to determine:
1. The *root cause* step (heuristic rule-based detection).
2. A *cascade chain* showing how a failure at step N propagates to N+1.
3. *Remediation suggestions* grounded in the failure mode.
4. A *counterfactual analysis* concept (what-if a step had passed).
5. An *LLM-as-judge* structured rubric concept for deeper diagnosis.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.core.config import StepStatus
from backend.core.tracer import Trajectory, TrajectoryStep

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class FailureMode(enum.StrEnum):
    """Categorised failure modes detected by the attribution engine."""

    TIMEOUT = "timeout"
    LOW_SCORE = "low_score"
    GUARDRAIL_VIOLATION = "guardrail_violation"
    EMPTY_RESULT = "empty_result"
    TOKEN_LIMIT = "token_limit"
    EXCEPTION = "exception"
    DEGRADATION = "degradation"
    UNKNOWN = "unknown"


class Severity(enum.StrEnum):
    """Impact severity of a root cause."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class CascadeLink:
    """One link in a failure cascade chain."""

    step_name: str
    failure_mode: FailureMode
    severity: Severity
    message: str
    propagated: bool  # True if this step failed *because* of upstream


@dataclass(slots=True)
class BlameReport:
    """Output of the blame attribution engine."""

    run_id: str
    root_cause_step: str
    root_cause_mode: FailureMode
    root_cause_message: str
    severity: Severity
    cascade_chain: list[CascadeLink] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    counterfactuals: list[dict[str, Any]] = field(default_factory=list)
    rubric: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0  # 0.0 = severe failure, 1.0 = fully healthy

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root_cause_step": self.root_cause_step,
            "root_cause_mode": str(self.root_cause_mode),
            "root_cause_message": self.root_cause_message,
            "severity": str(self.severity),
            "cascade_chain": [
                {
                    "step": c.step_name,
                    "failure_mode": str(c.failure_mode),
                    "severity": str(c.severity),
                    "message": c.message,
                    "propagated": c.propagated,
                }
                for c in self.cascade_chain
            ],
            "remediation": self.remediation,
            "counterfactuals": self.counterfactuals,
            "rubric": self.rubric,
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# Heuristic rules
# ---------------------------------------------------------------------------

# Each rule is (predicate, failure_mode, severity, message_template).
# A predicate receives (step, trajectory) and returns True if the rule fires.

def _rule_exception(step: TrajectoryStep, _traj: Trajectory) -> bool:
    return step.status == StepStatus.FAILED


def _rule_timeout(step: TrajectoryStep, _traj: Trajectory) -> bool:
    return step.status == StepStatus.TIMED_OUT


def _rule_low_score(step: TrajectoryStep, _traj: Trajectory) -> bool:
    score = step.metrics.score
    return score is not None and score < 0.4


def _rule_empty_result(step: TrajectoryStep, _traj: Trajectory) -> bool:
    payload = step.payload.get("result", {})
    if step.step_name == "retrieve":
        return payload.get("count", 1) == 0
    if step.step_name == "generate":
        return len(payload.get("text", "")) == 0
    return False


def _rule_guardrail_violation(step: TrajectoryStep, _traj: Trajectory) -> bool:
    if step.step_name != "guardrail":
        return False
    result = step.payload.get("result", {})
    return result.get("passed") is False


def _rule_token_limit(step: TrajectoryStep, _traj: Trajectory) -> bool:
    if step.status != StepStatus.FAILED:
        return False
    err = (step.error or "").lower()
    return "token" in err or "context_length" in err


@dataclass(frozen=True, slots=True)
class _Rule:
    predicate: Any  # (TrajectoryStep, Trajectory) -> bool
    failure_mode: FailureMode
    severity: Severity
    message_template: str


_HEURISTIC_RULES: list[_Rule] = [
    _Rule(_rule_exception, FailureMode.EXCEPTION, Severity.HIGH, "{error}"),
    _Rule(_rule_timeout, FailureMode.TIMEOUT, Severity.CRITICAL, "Step timed out"),
    _Rule(_rule_guardrail_violation, FailureMode.GUARDRAIL_VIOLATION, Severity.HIGH, "Guardrail blocked output"),
    _Rule(_rule_low_score, FailureMode.LOW_SCORE, Severity.MEDIUM, "Step quality score {score:.2f} below threshold"),
    _Rule(_rule_empty_result, FailureMode.EMPTY_RESULT, Severity.MEDIUM, "Step produced empty output"),
    _Rule(_rule_token_limit, FailureMode.TOKEN_LIMIT, Severity.HIGH, "Token limit exceeded: {error}"),
]

# Cascade propagation: probability multiplier applied at each downstream step.
# P(fail at N+1) += base_propagation * multiplier^(distance from root).
_BASE_PROPAGATION = 0.7
_PROPAGATION_MULTIPLIER = 0.6  # cascade decays with distance


# ---------------------------------------------------------------------------
# Blame attribution engine
# ---------------------------------------------------------------------------


class BlameAttributionEngine:
    """Analyses a trajectory and produces a :class:`BlameReport`.

    The engine is deliberately stateless — all inputs come via the trajectory
    and all outputs are captured in the report dataclass.
    """

    # -- heuristics ----------------------------------------------------------

    @staticmethod
    def _classify_step(
        step: TrajectoryStep,
        trajectory: Trajectory,
    ) -> _Rule | None:
        """Return the first matching heuristic rule for a step, or None."""
        for rule in _HEURISTIC_RULES:
            if rule.predicate(step, trajectory):
                return rule
        return None

    # -- cascade model -------------------------------------------------------

    @staticmethod
    def _build_cascade(
        root_index: int,
        steps: list[TrajectoryStep],
        root_rule: _Rule,
    ) -> list[CascadeLink]:
        """Build a cascade chain from *root_index* forward.

        Downstream steps that also failed receive ``propagated=True`` and a
        severity adjusted by the propagation decay model.
        """
        chain: list[CascadeLink] = []
        distance = 0
        for idx in range(root_index, len(steps)):
            step = steps[idx]
            distance = idx - root_index
            propagated = distance > 0

            # Compute propagated severity
            if propagated:
                if step.status == StepStatus.SUCCESS:
                    # Upstream failure did NOT cascade — note as survived
                    chain.append(
                        CascadeLink(
                            step_name=step.step_name,
                            failure_mode=FailureMode.DEGRADATION,
                            severity=Severity.LOW,
                            message="Step recovered despite upstream failure",
                            propagated=True,
                        )
                    )
                    continue
                severity = Severity.MEDIUM if distance <= 2 else Severity.LOW
            else:
                severity = root_rule.severity

            chain.append(
                CascadeLink(
                    step_name=step.step_name,
                    failure_mode=root_rule.failure_mode,
                    severity=severity,
                    message=root_rule.message_template.format(
                        error=step.error or "",
                        score=step.metrics.score or 0.0,
                    ),
                    propagated=propagated,
                )
            )
        return chain

    # -- counterfactual analysis ---------------------------------------------

    @staticmethod
    def _counterfactuals(
        root_index: int,
        steps: list[TrajectoryStep],
        trajectory: Trajectory,
    ) -> list[dict[str, Any]]:
        """Hypothetical: what if the failing step had succeeded?

        This is a lightweight heuristic — a full simulation would require
        re-execution.  We flag downstream steps that *might* have succeeded.
        """
        facts: list[dict[str, Any]] = []
        for idx in range(root_index + 1, len(steps)):
            step = steps[idx]
            if step.status != StepStatus.SUCCESS:
                facts.append(
                    {
                        "hypothetical_step": step.step_name,
                        "assumption": f"If '{steps[root_index].step_name}' had succeeded",
                        "possible_outcome": "This step may have produced a valid result",
                        "confidence": round(max(0.3, 0.9 - 0.15 * (idx - root_index)), 2),
                    }
                )
        return facts

    # -- LLM-as-judge rubric ------------------------------------------------

    @staticmethod
    def _build_rubric(
        trajectory: Trajectory,
        root_step: TrajectoryStep,
        failure_mode: FailureMode,
    ) -> dict[str, Any]:
        """Produce a structured rubric for LLM-as-judge evaluation.

        The rubric is a template that an external LLM evaluator can consume
        to give a more nuanced diagnosis.
        """
        return {
            "rubric_version": "1.0",
            "dimensions": [
                {
                    "name": "retrieval_quality",
                    "description": "Were the retrieved documents relevant to the query?",
                    "weight": 0.25,
                    "score_range": [0, 1],
                },
                {
                    "name": "rerank_precision",
                    "description": "Did reranking improve document ordering?",
                    "weight": 0.15,
                    "score_range": [0, 1],
                },
                {
                    "name": "reasoning_coherence",
                    "description": "Was the reasoning logical and grounded in context?",
                    "weight": 0.25,
                    "score_range": [0, 1],
                },
                {
                    "name": "guardrail_appropriateness",
                    "description": "Were guardrail decisions fair (not over/under-blocking)?",
                    "weight": 0.15,
                    "score_range": [0, 1],
                },
                {
                    "name": "generation_faithfulness",
                    "description": "Does the final answer faithfully reflect the context?",
                    "weight": 0.20,
                    "score_range": [0, 1],
                },
            ],
            "context": {
                "run_id": trajectory.run_id,
                "query": trajectory.metadata.get("query", "N/A"),
                "failed_step": root_step.step_name,
                "failure_mode": str(failure_mode),
                "error": root_step.error,
            },
            "instructions": (
                "Score each dimension 0–1. Focus especially on the dimension "
                "corresponding to the failed step. Provide a brief rationale "
                "for each score."
            ),
        }

    # -- remediation ---------------------------------------------------------

    @staticmethod
    def _suggest_remediation(
        root_step: TrajectoryStep,
        failure_mode: FailureMode,
    ) -> list[str]:
        """Return actionable remediation suggestions."""
        suggestions: list[str] = []
        match failure_mode:
            case FailureMode.TIMEOUT:
                suggestions.append("Increase timeout_seconds in the step config.")
                suggestions.append("Profile the step for bottlenecks and optimise.")
            case FailureMode.LOW_SCORE:
                suggestions.append("Review input quality (query, documents).")
                suggestions.append("Tune similarity_threshold or reranker model.")
            case FailureMode.GUARDRAIL_VIOLATION:
                violations = root_step.payload.get("result", {}).get("violations", [])
                if violations:
                    suggestions.append(f"Review violations: {violations}")
                suggestions.append("Consider adjusting guardrail thresholds or using fail_open.")
            case FailureMode.EMPTY_RESULT:
                suggestions.append("Check that the upstream step produced non-empty output.")
                suggestions.append("Verify index exists and contains the expected data.")
            case FailureMode.TOKEN_LIMIT:
                suggestions.append("Reduce top_k or truncate context before this step.")
                suggestions.append("Use a model with a larger context window.")
            case FailureMode.EXCEPTION:
                suggestions.append("Inspect the stack trace and add error handling.")
                suggestions.append("Add retry logic for transient errors.")
            case FailureMode.DEGRADATION:
                suggestions.append("Investigate why this step degraded despite upstream recovery.")
            case _:
                suggestions.append("Review logs for the failing step and check configuration.")
        return suggestions

    # -- public API ----------------------------------------------------------

    def analyse(self, trajectory: Trajectory) -> BlameReport:
        """Produce a full :class:`BlameReport` from a trajectory.

        Steps:
        1. Find the first failing step (root cause).
        2. Classify it via heuristic rules.
        3. Build the cascade chain.
        4. Compute counterfactuals.
        5. Generate remediation suggestions and the LLM-as-judge rubric.
        """
        steps = trajectory.steps

        # If no failures, return a healthy report.
        failed = [i for i, s in enumerate(steps) if s.status != StepStatus.SUCCESS]
        if not failed:
            return BlameReport(
                run_id=trajectory.run_id,
                root_cause_step="none",
                root_cause_mode=FailureMode.UNKNOWN,
                root_cause_message="All steps completed successfully.",
                severity=Severity.LOW,
                score=1.0,
            )

        root_index = failed[0]
        root_step = steps[root_index]
        rule = self._classify_step(root_step, trajectory)

        if rule is None:
            failure_mode = FailureMode.UNKNOWN
            severity = Severity.MEDIUM
            message = f"Step '{root_step.step_name}' failed without matching any heuristic rule."
        else:
            failure_mode = rule.failure_mode
            severity = rule.severity
            message = rule.message_template.format(
                error=root_step.error or "",
                score=root_step.metrics.score or 0.0,
            )

        cascade = self._build_cascade(root_index, steps, rule) if rule else []
        counterfactuals = self._counterfactuals(root_index, steps, trajectory)
        remediation = self._suggest_remediation(root_step, failure_mode)
        rubric = self._build_rubric(trajectory, root_step, failure_mode)

        # Score: 1.0 minus penalties for each failed / propagated step
        penalty = 0.0
        for link in cascade:
            if link.severity == Severity.CRITICAL:
                penalty += 0.4
            elif link.severity == Severity.HIGH:
                penalty += 0.25
            elif link.severity == Severity.MEDIUM:
                penalty += 0.15
            else:
                penalty += 0.05
        final_score = max(0.0, 1.0 - penalty)

        report = BlameReport(
            run_id=trajectory.run_id,
            root_cause_step=root_step.step_name,
            root_cause_mode=failure_mode,
            root_cause_message=message,
            severity=severity,
            cascade_chain=cascade,
            remediation=remediation,
            counterfactuals=counterfactuals,
            rubric=rubric,
            score=round(final_score, 3),
        )

        logger.info(
            "blame_analysis_complete",
            run_id=trajectory.run_id,
            root_cause=root_step.step_name,
            failure_mode=str(failure_mode),
            severity=str(severity),
            score=report.score,
        )
        return report
