"""Context relevance metric — checks if retrieved context is relevant to the query."""

from __future__ import annotations

import structlog

from backend.eval.metrics.base import BaseMetric
from backend.eval.models import Step, StepScore, StepType, Trajectory
from backend.eval.similarity import (
    EmbeddingsUnavailableError,
    embed_similarity_to_chunks,
)

logger = structlog.get_logger(__name__)

_VALID_SIM_MODES = ("token", "embedding", "hybrid")


class ContextRelevanceMetric(BaseMetric):
    """Measure how relevant the retrieved context is to the user query.

    Scoring approach:
    1. For each retrieval step, compute token-overlap similarity between
       the query and each context chunk.
    2. Average across chunks → per-step score.
    3. Aggregate: mean of retrieval step scores (non-retrieval steps ignored).
    """

    name = "context_relevance"
    description = (
        "Measures how relevant the retrieved context is to the original query. "
        "1.0 = all retrieved chunks are highly relevant, 0.0 = none are relevant."
    )

    def __init__(
        self,
        *,
        min_relevance: float = 0.05,
        similarity_mode: str = "token",
        **config,
    ) -> None:
        if similarity_mode not in _VALID_SIM_MODES:
            raise ValueError(
                f"similarity_mode must be one of {_VALID_SIM_MODES}, "
                f"got {similarity_mode!r}"
            )
        super().__init__(
            min_relevance=min_relevance,
            similarity_mode=similarity_mode,
            **config,
        )
        self.min_relevance = min_relevance
        self.similarity_mode = similarity_mode

    # ------------------------------------------------------------------
    # Per-step scoring
    # ------------------------------------------------------------------

    def score_step(self, trajectory: Trajectory, step: Step) -> StepScore:
        query = trajectory.query

        if step.step_type != StepType.RETRIEVAL:
            return StepScore(
                step_id=step.step_id,
                metric_name=self.name,
                score=1.0,
                details="Non-retrieval step — skipped.",
            )

        chunks = step.context_chunks
        if not chunks:
            return StepScore(
                step_id=step.step_id,
                metric_name=self.name,
                score=0.0,
                details="Retrieval step with no context chunks.",
            )

        chunk_scores = [self._similarity(query, chunk) for chunk in chunks]
        avg_score = sum(chunk_scores) / len(chunk_scores)
        relevant_count = sum(
            1 for s in chunk_scores if s >= self.min_relevance
        )

        return StepScore(
            step_id=step.step_id,
            metric_name=self.name,
            score=round(self.clamp(avg_score), 4),
            details=(
                f"{relevant_count}/{len(chunks)} chunks above "
                f"relevance threshold ({self.min_relevance})."
            ),
            breakdown={
                "chunk_count": len(chunks),
                "avg_relevance": round(avg_score, 4),
                "relevant_chunks": relevant_count,
                "per_chunk_scores": [round(s, 4) for s in chunk_scores],
            },
        )

    # ------------------------------------------------------------------
    # Override: only count retrieval steps
    # ------------------------------------------------------------------

    def _is_relevant(self, step_score: StepScore) -> bool:
        return step_score.details != "Non-retrieval step — skipped."

    def aggregate_steps(
        self,
        trajectory: Trajectory,
        step_scores: list[StepScore],
    ) -> float:
        """Mean of retrieval-step scores only."""
        retrieval_scores = [
            s.score
            for s in step_scores
            if s.details != "Non-retrieval step — skipped."
        ]
        if not retrieval_scores:
            # Fall back to trajectory-level retrieved context.
            return self._score_trajectory_level(trajectory)
        return round(sum(retrieval_scores) / len(retrieval_scores), 4)

    def _similarity(self, query: str, chunk: str) -> float:
        """Mode-aware similarity between *query* and a single *chunk*.

        ``"token"`` / fallback → Jaccard token overlap. ``"embedding"`` → cosine
        via the embedding backend. ``"hybrid"`` → max of the two. Falls back to
        token overlap when the embedding backend is unavailable.
        """
        if self.similarity_mode == "token":
            return self.token_overlap(query, chunk)
        try:
            sims = embed_similarity_to_chunks(query, [chunk])
        except EmbeddingsUnavailableError:
            sims = []
        if sims:
            cos = sims[0]
            if self.similarity_mode == "embedding":
                return cos
            return max(self.token_overlap(query, chunk), cos)
        return self.token_overlap(query, chunk)

    def _score_trajectory_level(self, trajectory: Trajectory) -> float:
        """Score trajectory-level retrieved_context when no retrieval steps exist."""
        chunks = trajectory.retrieved_context
        if not chunks or not trajectory.query:
            return 0.0
        chunk_scores = [
            self._similarity(trajectory.query, chunk) for chunk in chunks
        ]
        return round(sum(chunk_scores) / len(chunk_scores), 4)
