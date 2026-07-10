"""Faithfulness metric — checks if the generated answer is grounded in retrieved context."""

from __future__ import annotations

import re

import structlog

from backend.eval.metrics.base import BaseMetric
from backend.eval.models import Step, StepScore, StepType, Trajectory

logger = structlog.get_logger(__name__)

# Rough sentence splitter — handles abbreviations poorly but fine for eval text.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class FaithfulnessMetric(BaseMetric):
    """Evaluate whether the final answer is supported by retrieved context.

    Scoring approach:
    1. Extract declarative claims from the answer.
    2. For each claim, measure overlap with the context chunks.
    3. Score = fraction of claims that are grounded.
    """

    name = "faithfulness"
    description = (
        "Measures how well the generated answer is grounded in the retrieved context. "
        "1.0 = every claim is supported, 0.0 = no claims are supported."
    )

    def __init__(self, *, overlap_threshold: float = 0.15, **config) -> None:
        super().__init__(overlap_threshold=overlap_threshold, **config)
        self.overlap_threshold = overlap_threshold

    # ------------------------------------------------------------------
    # Per-step scoring
    # ------------------------------------------------------------------

    def score_step(self, trajectory: Trajectory, step: Step) -> StepScore:
        """Score only the ANSWER step; other steps get a neutral score."""
        if step.step_type != StepType.ANSWER:
            return StepScore(
                step_id=step.step_id,
                metric_name=self.name,
                score=1.0,
                details="Non-answer step — skipped.",
            )

        context_text = self._gather_context(trajectory)
        answer_text = step.output_text or trajectory.final_answer
        claims = self._extract_claims(answer_text)
        if not claims:
            return StepScore(
                step_id=step.step_id,
                metric_name=self.name,
                score=1.0,
                details="No claims extracted from answer.",
            )

        supported = sum(
            1 for claim in claims if self._is_supported(claim, context_text)
        )
        score = supported / len(claims)
        return StepScore(
            step_id=step.step_id,
            metric_name=self.name,
            score=round(score, 4),
            details=f"{supported}/{len(claims)} claims grounded in context.",
            breakdown={
                "total_claims": len(claims),
                "supported_claims": supported,
                "unsupported_claims": len(claims) - supported,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _gather_context(trajectory: Trajectory) -> str:
        """Collect all context from retrieval steps and the trajectory-level field."""
        chunks: list[str] = list(trajectory.retrieved_context)
        for step in trajectory.steps:
            if step.step_type == StepType.RETRIEVAL:
                chunks.extend(step.context_chunks)
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_claims(text: str) -> list[str]:
        """Split *text* into rough declarative claims (sentences)."""
        sentences = _SENTENCE_RE.split(text.strip())
        # Filter out very short fragments that are likely not real claims.
        return [s.strip() for s in sentences if len(s.strip()) > 10]

    def _is_supported(self, claim: str, context: str) -> bool:
        """Check if at least one chunk of *context* has sufficient overlap with *claim*."""
        if not context:
            return False
        # Split context into chunks (double-newline or single-newline separated)
        chunks = re.split(r"\n{2,}|\n", context)
        for chunk in chunks:
            overlap = self.token_overlap(claim, chunk)
            if overlap >= self.overlap_threshold:
                return True
        return False
