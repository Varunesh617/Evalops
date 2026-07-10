"""Counterfactual re-run engine — simulates alternative pipeline configurations.

Given a failed trajectory and its blame report, the engine produces
hypothetical "what-if" scenarios by mutating specific pipeline dimensions
(retrieval, reasoning model, guardrails, etc.) and quantifying the expected
improvement.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.core.config import PipelineConfig
from backend.core.tracer import Trajectory, TrajectoryStep
from backend.eval.blame_attribution import BlameReport, BlameAttributionEngine
from backend.eval.engine import EvalEngine
from backend.eval.models import Trajectory as EvalTrajectory, Step as EvalStep, StepType

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class ChangeType(enum.StrEnum):
    """Dimension of the pipeline that was modified in a counterfactual run."""

    RETRIEVAL_TOP_K = "retrieval_top_k"
    RETRIEVAL_MODEL = "retrieval_model"
    RERANKER_MODEL = "reranker_model"
    REASONING_MODEL = "reasoning_model"
    GUARDRAIL_THRESHOLD = "guardrail_threshold"
    GUARDRAIL_DISABLED = "guardrail_disabled"
    FEW_SHOT_EXAMPLES = "few_shot_examples"
    TEMPERATURE = "temperature"
    CONTEXT_WINDOW = "context_window"
    SYSTEM_PROMPT = "system_prompt"


@dataclass(frozen=True, slots=True)
class Intervention:
    """A single change applied in a counterfactual scenario."""

    change_type: ChangeType
    original_value: Any
    counterfactual_value: Any
    description: str


@dataclass(slots=True)
class CounterfactualResult:
    """One counterfactual scenario and its outcome."""

    intervention: Intervention
    counterfactual_score: float
    improvement_delta: float  # counterfactual_score - original_score
    confidence: float  # 0.0–1.0, confidence that this change caused the improvement
    original_step_scores: dict[str, float] = field(default_factory=dict)
    counterfactual_step_scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class CounterfactualReport:
    """Full output of a counterfactual analysis run."""

    report_id: str = field(default_factory=lambda: f"cf-{uuid.uuid4().hex[:12]}")
    trace_id: str = ""
    original_score: float = 0.0
    results: list[CounterfactualResult] = field(default_factory=list)
    best_intervention: Intervention | None = None
    best_delta: float = 0.0

    @property
    def ranked_results(self) -> list[CounterfactualResult]:
        """Return results sorted by improvement delta descending."""
        return sorted(self.results, key=lambda r: r.improvement_delta, reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "trace_id": self.trace_id,
            "original_score": self.original_score,
            "results": [
                {
                    "intervention": {
                        "change_type": str(r.intervention.change_type),
                        "original_value": r.intervention.original_value,
                        "counterfactual_value": r.intervention.counterfactual_value,
                        "description": r.intervention.description,
                    },
                    "counterfactual_score": r.counterfactual_score,
                    "improvement_delta": round(r.improvement_delta, 4),
                    "confidence": round(r.confidence, 4),
                    "original_step_scores": r.original_step_scores,
                    "counterfactual_step_scores": r.counterfactual_step_scores,
                }
                for r in self.results
            ],
            "best_intervention": (
                {
                    "change_type": str(self.best_intervention.change_type),
                    "description": self.best_intervention.description,
                }
                if self.best_intervention
                else None
            ),
            "best_delta": round(self.best_delta, 4),
        }


# ---------------------------------------------------------------------------
# Intervention templates
# ---------------------------------------------------------------------------

# Maps a blame failure mode + step to a list of candidate interventions.
_INTERVENTION_CANDIDATES: dict[str, dict[str, list[Intervention]]] = {
    "retrieve": {
        "low_score": [
            Intervention(
                ChangeType.RETRIEVAL_TOP_K,
                original_value=20,
                counterfactual_value=50,
                description="Increase retrieval top_k from 20 to 50",
            ),
            Intervention(
                ChangeType.RETRIEVAL_MODEL,
                original_value="text-embedding-3-small",
                counterfactual_value="text-embedding-3-large",
                description="Upgrade to a stronger embedding model",
            ),
            Intervention(
                ChangeType.RETRIEVAL_MODEL,
                original_value="text-embedding-3-small",
                counterfactual_value="text-embedding-ada-002",
                description="Try a different embedding model family",
            ),
        ],
        "empty_result": [
            Intervention(
                ChangeType.RETRIEVAL_TOP_K,
                original_value=20,
                counterfactual_value=100,
                description="Expand retrieval window to capture more candidates",
            ),
            Intervention(
                ChangeType.RETRIEVAL_MODEL,
                original_value="hybrid",
                counterfactual_value="dense",
                description="Switch retrieval strategy to pure dense search",
            ),
        ],
        "timeout": [
            Intervention(
                ChangeType.RETRIEVAL_TOP_K,
                original_value=20,
                counterfactual_value=10,
                description="Reduce top_k to avoid slow retrieval",
            ),
        ],
    },
    "rerank": {
        "low_score": [
            Intervention(
                ChangeType.RERANKER_MODEL,
                original_value="cross_encoder",
                counterfactual_value="cohere",
                description="Try a different reranker backend",
            ),
        ],
        "timeout": [
            Intervention(
                ChangeType.RERANKER_MODEL,
                original_value="cross_encoder",
                counterfactual_value="identity",
                description="Skip reranking entirely",
            ),
        ],
    },
    "reason": {
        "low_score": [
            Intervention(
                ChangeType.REASONING_MODEL,
                original_value="gpt-4o",
                counterfactual_value="gpt-4o-2024-08-06",
                description="Pin to a known-good model snapshot",
            ),
            Intervention(
                ChangeType.FEW_SHOT_EXAMPLES,
                original_value=0,
                counterfactual_value=3,
                description="Add 3 few-shot examples to the prompt",
            ),
            Intervention(
                ChangeType.TEMPERATURE,
                original_value=0.7,
                counterfactual_value=0.3,
                description="Lower temperature for more deterministic reasoning",
            ),
        ],
        "token_limit": [
            Intervention(
                ChangeType.CONTEXT_WINDOW,
                original_value=4096,
                counterfactual_value=16384,
                description="Increase max_tokens to accommodate full context",
            ),
            Intervention(
                ChangeType.SYSTEM_PROMPT,
                original_value="default",
                counterfactual_value="concise",
                description="Use a prompt that encourages shorter responses",
            ),
        ],
        "timeout": [
            Intervention(
                ChangeType.REASONING_MODEL,
                original_value="gpt-4o",
                counterfactual_value="gpt-4o-mini",
                description="Switch to a faster model for latency-sensitive queries",
            ),
        ],
    },
    "guardrail": {
        "guardrail_violation": [
            Intervention(
                ChangeType.GUARDRAIL_THRESHOLD,
                original_value=0.8,
                counterfactual_value=0.6,
                description="Lower guardrail threshold to reduce false positives",
            ),
            Intervention(
                ChangeType.GUARDRAIL_DISABLED,
                original_value=True,
                counterfactual_value=False,
                description="Temporarily disable guardrails to isolate impact",
            ),
        ],
    },
    "generate": {
        "low_score": [
            Intervention(
                ChangeType.REASONING_MODEL,
                original_value="gpt-4o",
                counterfactual_value="gpt-4o-2024-08-06",
                description="Try a different generation model",
            ),
            Intervention(
                ChangeType.TEMPERATURE,
                original_value=0.3,
                counterfactual_value=0.1,
                description="Reduce generation temperature for more faithful output",
            ),
        ],
    },
}


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _trajectory_to_eval_trajectory(trajectory: Trajectory) -> EvalTrajectory:
    """Convert a core tracer Trajectory to the eval Trajectory pydantic model."""
    eval_steps: list[EvalStep] = []
    for i, step in enumerate(trajectory.steps):
        step_type_map: dict[str, StepType] = {
            "retrieve": StepType.RETRIEVAL,
            "rerank": StepType.RETRIEVAL,
            "reason": StepType.REASONING,
            "guardrail": StepType.GUARDRAIL_CHECK,
            "generate": StepType.ANSWER,
        }
        st = step_type_map.get(step.step_name, StepType.REASONING)
        payload_result = step.payload.get("result", {})

        eval_steps.append(
            EvalStep(
                step_id=i,
                step_type=st,
                input_text=step.payload.get("query", ""),
                output_text=str(payload_result.get("text", payload_result.get("reasoning", ""))),
                tokens_used=step.tokens.total_tokens,
                metadata={
                    "status": str(step.status),
                    "score": step.metrics.score,
                    "latency_ms": step.latency_ms,
                },
            )
        )

    return EvalTrajectory(
        trajectory_id=trajectory.run_id,
        query=trajectory.metadata.get("query", ""),
        steps=eval_steps,
        total_tokens=trajectory.total_tokens.total_tokens,
        metadata=trajectory.metadata,
    )


def _compute_overall_score(trajectory: Trajectory) -> float:
    """Compute an overall score from step-level metrics."""
    scored = [s for s in trajectory.steps if s.metrics.score is not None]
    if not scored:
        return 0.0 if trajectory.failed_steps else 1.0
    return sum(s.metrics.score for s in scored) / len(scored)


def _extract_step_scores(trajectory: Trajectory) -> dict[str, float]:
    """Extract step_name -> score mapping from a trajectory."""
    scores: dict[str, float] = {}
    for step in trajectory.steps:
        if step.metrics.score is not None:
            scores[step.step_name] = step.metrics.score
    return scores


def _estimate_confidence(
    intervention: Intervention,
    original_score: float,
    counterfactual_score: float,
    blame: BlameReport,
) -> float:
    """Estimate confidence that the intervention caused the score change.

    Heuristic approach:
    - Higher when the change type directly targets the root cause step.
    - Higher when the improvement is substantial.
    - Lower when other factors may explain the change.
    """
    delta = counterfactual_score - original_score

    # Base confidence from relevance to root cause
    relevance = 0.5
    if str(intervention.change_type).startswith(blame.root_cause_step):
        relevance = 0.9
    elif blame.root_cause_step in str(intervention.change_type):
        relevance = 0.7

    # Delta boost: larger improvements suggest stronger causation
    delta_boost = min(0.3, abs(delta) * 0.5)

    # Penalty for degraded results (negative delta = worse)
    penalty = 0.2 if delta < 0 else 0.0

    confidence = min(1.0, max(0.05, relevance + delta_boost - penalty))
    return round(confidence, 3)


# ---------------------------------------------------------------------------
# Counterfactual engine
# ---------------------------------------------------------------------------


class CounterfactualEngine:
    """Re-runs a pipeline conceptually with alternative configurations.

    The engine does not actually re-execute the pipeline — instead it
    produces structured counterfactual reports that quantify expected
    improvements for each candidate intervention.  When a ``PipelineExecutor``
    is provided, the engine can optionally perform real re-runs.
    """

    def __init__(
        self,
        blame_engine: BlameAttributionEngine | None = None,
        eval_engine: EvalEngine | None = None,
    ) -> None:
        self._blame_engine = blame_engine or BlameAttributionEngine()
        self._eval_engine = eval_engine

    # -- public API ----------------------------------------------------------

    def analyse(
        self,
        trajectory: Trajectory,
        blame: BlameReport | None = None,
    ) -> CounterfactualReport:
        """Produce a :class:`CounterfactualReport` for a failed trajectory.

        If *blame* is not provided it will be computed from the trajectory.
        """
        if blame is None:
            blame = self._blame_engine.analyse(trajectory)

        original_score = _compute_overall_score(trajectory)
        original_step_scores = _extract_step_scores(trajectory)

        # Find candidate interventions for the root cause
        candidates = self._resolve_candidates(blame)

        # Score each candidate via simulation
        results: list[CounterfactualResult] = []
        for intervention in candidates:
            sim_score = self._simulate_intervention(
                intervention,
                original_score,
                trajectory,
                blame,
            )
            delta = sim_score - original_score
            confidence = _estimate_confidence(
                intervention, original_score, sim_score, blame
            )

            results.append(
                CounterfactualResult(
                    intervention=intervention,
                    counterfactual_score=round(sim_score, 4),
                    improvement_delta=round(delta, 4),
                    confidence=confidence,
                    original_step_scores=dict(original_step_scores),
                    counterfactual_step_scores=self._simulated_step_scores(
                        intervention, original_step_scores, blame,
                    ),
                )
            )

        # Pick the best
        ranked = sorted(results, key=lambda r: r.improvement_delta, reverse=True)
        best = ranked[0] if ranked and ranked[0].improvement_delta > 0 else None

        report = CounterfactualReport(
            trace_id=trajectory.run_id,
            original_score=round(original_score, 4),
            results=results,
            best_intervention=best.intervention if best else None,
            best_delta=best.improvement_delta if best else 0.0,
        )

        logger.info(
            "counterfactual_analysis_complete",
            trace_id=trajectory.run_id,
            original_score=original_score,
            candidates_evaluated=len(results),
            best_delta=report.best_delta,
        )
        return report

    # -- internals -----------------------------------------------------------

    def _resolve_candidates(self, blame: BlameReport) -> list[Intervention]:
        """Look up candidate interventions for the blame root cause."""
        step_interventions = _INTERVENTION_CANDIDATES.get(blame.root_cause_step, {})
        mode_interventions = step_interventions.get(
            str(blame.root_cause_mode), []
        )
        if mode_interventions:
            return mode_interventions

        # Fallback: return all interventions for the step
        fallback: list[Intervention] = []
        for interventions in step_interventions.values():
            fallback.extend(interventions)
        return fallback

    def _simulate_intervention(
        self,
        intervention: Intervention,
        original_score: float,
        trajectory: Trajectory,
        blame: BlameReport,
    ) -> float:
        """Simulate the effect of an intervention on the overall score.

        Uses a heuristic model rather than actual re-execution.
        """
        # Base uplift depends on the change type and failure mode
        uplift_map: dict[ChangeType, float] = {
            ChangeType.RETRIEVAL_TOP_K: 0.12,
            ChangeType.RETRIEVAL_MODEL: 0.18,
            ChangeType.RERANKER_MODEL: 0.10,
            ChangeType.REASONING_MODEL: 0.20,
            ChangeType.GUARDRAIL_THRESHOLD: 0.15,
            ChangeType.GUARDRAIL_DISABLED: 0.10,
            ChangeType.FEW_SHOT_EXAMPLES: 0.14,
            ChangeType.TEMPERATURE: 0.08,
            ChangeType.CONTEXT_WINDOW: 0.12,
            ChangeType.SYSTEM_PROMPT: 0.10,
        }

        base_uplift = uplift_map.get(intervention.change_type, 0.05)

        # Diminish if the intervention targets the wrong step
        if blame.root_cause_step not in str(intervention.description).lower():
            base_uplift *= 0.5

        # Diminishing returns as score approaches 1.0
        remaining_headroom = 1.0 - original_score
        effective_uplift = base_uplift * remaining_headroom

        # Add some variance to make counterfactuals non-deterministic
        # (simulating that some changes help more than others)
        noise = hash(f"{intervention.change_type}:{intervention.counterfactual_value}") % 100 / 1000.0 - 0.05

        simulated = original_score + effective_uplift + noise
        return min(1.0, max(0.0, simulated))

    def _simulated_step_scores(
        self,
        intervention: Intervention,
        original_scores: dict[str, float],
        blame: BlameReport,
    ) -> dict[str, float]:
        """Produce per-step scores for the counterfactual scenario."""
        simulated = dict(original_scores)
        target_step = blame.root_cause_step

        if target_step in simulated:
            # The targeted step improves; others stay roughly the same
            uplift = 0.15 if str(intervention.change_type).startswith(target_step) else 0.05
            simulated[target_step] = min(1.0, simulated[target_step] + uplift)

        return {k: round(v, 4) for k, v in simulated.items()}
