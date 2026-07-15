"""Recommendation engine — maps failure patterns to actionable fixes.

Consumes a :class:`BlameReport` and optionally a :class:`CounterfactualReport`
to produce prioritised, actionable recommendations with implementation snippets.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.diagnosis.counterfactual import ChangeType
from backend.eval.blame_attribution import BlameReport, FailureMode, Severity

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class RecommendationCategory(enum.StrEnum):
    RETRIEVAL = "retrieval"
    REASONING = "reasoning"
    GUARDRAIL = "guardrail"
    COST = "cost"
    PERFORMANCE = "performance"
    GENERAL = "general"


class Impact(enum.StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Difficulty(enum.StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


# Rough per-step cost/latency deltas (USD, ms) introduced by an intervention.
# Used to surface a cost/latency tradeoff next to each recommendation (3.5).
# Negative values mean a saving; positive mean an added cost/latency.
_ESTIMATED_COST_DELTA: dict[ChangeType, float] = {
    ChangeType.RETRIEVAL_TOP_K: 0.0005,  # more candidates => more token cost
    ChangeType.RETRIEVAL_MODEL: 0.0040,  # stronger embedding model is pricier
    ChangeType.RERANKER_MODEL: 0.0020,
    ChangeType.REASONING_MODEL: 0.0120,  # bigger reasoning model is the cost driver
    ChangeType.GUARDRAIL_THRESHOLD: 0.0,
    ChangeType.GUARDRAIL_DISABLED: -0.0010,  # fewer blocked reruns
    ChangeType.FEW_SHOT_EXAMPLES: 0.0015,  # more prompt tokens
    ChangeType.TEMPERATURE: 0.0,
    ChangeType.CONTEXT_WINDOW: 0.0030,  # more context tokens
    ChangeType.SYSTEM_PROMPT: 0.0008,
}

_ESTIMATED_LATENCY_DELTA_MS: dict[ChangeType, float] = {
    ChangeType.RETRIEVAL_TOP_K: 120.0,
    ChangeType.RETRIEVAL_MODEL: 200.0,
    ChangeType.RERANKER_MODEL: 150.0,
    ChangeType.REASONING_MODEL: 600.0,
    ChangeType.GUARDRAIL_THRESHOLD: 0.0,
    ChangeType.GUARDRAIL_DISABLED: -80.0,
    ChangeType.FEW_SHOT_EXAMPLES: 100.0,
    ChangeType.TEMPERATURE: 0.0,
    ChangeType.CONTEXT_WINDOW: 250.0,
    ChangeType.SYSTEM_PROMPT: 40.0,
}


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A single actionable recommendation."""

    category: RecommendationCategory
    action: str
    impact: Impact
    difficulty: Difficulty
    snippet: str = ""
    rationale: str = ""
    priority: int = 0  # higher = more important
    change_type: str | None = None  # maps to a counterfactual ChangeType when known
    estimated_cost_delta_usd: float | None = None
    estimated_latency_delta_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": str(self.category),
            "action": self.action,
            "impact": str(self.impact),
            "difficulty": str(self.difficulty),
            "snippet": self.snippet,
            "rationale": self.rationale,
            "priority": self.priority,
            "change_type": self.change_type,
            "estimated_cost_delta_usd": self.estimated_cost_delta_usd,
            "estimated_latency_delta_ms": self.estimated_latency_delta_ms,
        }


@dataclass(slots=True)
class RecommendationReport:
    """Ordered list of recommendations for a failure."""

    trace_id: str = ""
    recommendations: list[Recommendation] = field(default_factory=list)

    @property
    def prioritised(self) -> list[Recommendation]:
        return sorted(self.recommendations, key=lambda r: r.priority, reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "recommendations": [r.to_dict() for r in self.prioritised],
            "total": len(self.recommendations),
        }


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Rule:
    failure_mode: FailureMode
    step_name: str | None  # None = any step
    category: RecommendationCategory
    action: str
    impact: Impact
    difficulty: Difficulty
    snippet: str
    rationale: str
    priority: int
    change_type: ChangeType | None = None  # linked counterfactual change type


def _priority_for_severity(severity: Severity) -> int:
    return {
        Severity.CRITICAL: 100,
        Severity.HIGH: 80,
        Severity.MEDIUM: 50,
        Severity.LOW: 20,
    }.get(severity, 10)


_RULES: list[_Rule] = [
    # --- Retrieval failures ---
    _Rule(
        FailureMode.LOW_SCORE,
        "retrieve",
        RecommendationCategory.RETRIEVAL,
        "Increase retrieval top_k to capture more candidate documents",
        Impact.MEDIUM,
        Difficulty.EASY,
        snippet=(
            "# In your pipeline config:\n"
            "retrieval.top_k = 40  # was 20\n"
        ),
        rationale="A larger candidate pool increases the chance that relevant documents survive reranking.",
        priority=70,
        change_type=ChangeType.RETRIEVAL_TOP_K,
    ),
    _Rule(
        FailureMode.LOW_SCORE,
        "retrieve",
        RecommendationCategory.RETRIEVAL,
        "Upgrade to a stronger embedding model",
        Impact.HIGH,
        Difficulty.MEDIUM,
        snippet=(
            "# Switch embedding model:\n"
            'retrieval.embedding_model = "text-embedding-3-large"\n'
            "# Or for Cohere:\n"
            '# retrieval.embedding_model = "embed-v4"\n'
        ),
        rationale="Higher-quality embeddings produce better similarity scores and more relevant retrievals.",
        priority=75,
        change_type=ChangeType.RETRIEVAL_MODEL,
    ),
    _Rule(
        FailureMode.LOW_SCORE,
        "retrieve",
        RecommendationCategory.RETRIEVAL,
        "Add document preprocessing (chunking, deduplication)",
        Impact.MEDIUM,
        Difficulty.HARD,
        snippet=(
            "# Preprocessing pipeline:\n"
            "1. Split documents into 512-token chunks with 50-token overlap\n"
            "2. Remove near-duplicate chunks (cosine > 0.95)\n"
            "3. Filter chunks shorter than 50 tokens\n"
            "4. Re-index the cleaned corpus"
        ),
        rationale="Cleaner index data reduces noise and improves retrieval precision.",
        priority=60,
    ),
    _Rule(
        FailureMode.EMPTY_RESULT,
        "retrieve",
        RecommendationCategory.RETRIEVAL,
        "Expand retrieval window and check index coverage",
        Impact.HIGH,
        Difficulty.EASY,
        snippet=(
            "# Increase search scope:\n"
            "retrieval.top_k = 100\n"
            "retrieval.similarity_threshold = 0.5  # lower threshold\n"
            "\n"
            "# Then verify index coverage:\n"
            "# - Check that all expected documents are indexed\n"
            "# - Validate embedding generation succeeded"
        ),
        rationale="Empty results often mean the index is incomplete or the threshold is too strict.",
        priority=80,
    ),
    _Rule(
        FailureMode.TIMEOUT,
        "retrieve",
        RecommendationCategory.PERFORMANCE,
        "Reduce retrieval scope to stay within latency budget",
        Impact.MEDIUM,
        Difficulty.EASY,
        snippet=(
            "# Reduce scope:\n"
            "retrieval.top_k = 10  # was 20\n"
            "retrieval.timeout_seconds = 15.0  # was 30.0\n"
        ),
        rationale="Smaller candidate sets execute faster and avoid timeout cascades.",
        priority=65,
        change_type=ChangeType.RETRIEVAL_TOP_K,
    ),
    # --- Reasoning failures ---
    _Rule(
        FailureMode.LOW_SCORE,
        "reason",
        RecommendationCategory.REASONING,
        "Switch to a stronger reasoning model",
        Impact.HIGH,
        Difficulty.EASY,
        snippet=(
            "# Upgrade model:\n"
            'agent.model = "gpt-4o-2024-08-06"\n'
            "# Or for Claude:\n"
            '# agent.model = "claude-sonnet-4-20250514"\n'
        ),
        rationale="Stronger models produce more coherent, grounded reasoning from the same context.",
        priority=80,
        change_type=ChangeType.REASONING_MODEL,
    ),
    _Rule(
        FailureMode.LOW_SCORE,
        "reason",
        RecommendationCategory.REASONING,
        "Add few-shot examples to the prompt",
        Impact.MEDIUM,
        Difficulty.EASY,
        snippet=(
            '# Add few-shot examples to system_prompt:\n'
            'agent.system_prompt = (\n'
            '    "You are a helpful assistant.\\n\\n"\n'
            '    "Example 1:\\n"\n'
            '    "Q: What is X?\\n"\n'
            '    "A: [grounded answer with citation]\\n\\n"\n'
            '    "Example 2:\\n"\n'
            '    "Q: How does Y work?\\n"\n'
            '    "A: [grounded answer with citation]\\n"\n'
            ')\n'
        ),
        rationale="Few-shot examples guide the model toward the desired output format and reasoning style.",
        priority=70,
        change_type=ChangeType.FEW_SHOT_EXAMPLES,
    ),
    _Rule(
        FailureMode.LOW_SCORE,
        "reason",
        RecommendationCategory.REASONING,
        "Simplify the prompt to reduce reasoning burden",
        Impact.MEDIUM,
        Difficulty.MEDIUM,
        snippet=(
            "# Simplified system prompt:\n"
            'agent.system_prompt = (\n'
            '    "Answer the question using ONLY the provided context.\\n"\n'
            '    "If the context does not contain the answer, say so.\\n"\n'
            '    "Cite your sources [1], [2], etc."\n'
            ')\n'
        ),
        rationale="Concise prompts reduce ambiguity and help the model stay grounded in retrieved context.",
        priority=65,
    ),
    _Rule(
        FailureMode.TOKEN_LIMIT,
        "reason",
        RecommendationCategory.REASONING,
        "Increase context window or truncate input",
        Impact.HIGH,
        Difficulty.EASY,
        snippet=(
            "# Option A: Increase max tokens\n"
            "agent.max_tokens = 8192  # was 4096\n"
            "\n"
            "# Option B: Truncate context before reasoning\n"
            "# Limit to top 5 reranked documents\n"
            "reranker.top_k = 5  # was 10\n"
        ),
        rationale="Token limit failures require either more budget or less input — both are straightforward fixes.",
        priority=85,
    ),
    _Rule(
        FailureMode.TIMEOUT,
        "reason",
        RecommendationCategory.PERFORMANCE,
        "Use a faster model for latency-sensitive queries",
        Impact.MEDIUM,
        Difficulty.EASY,
        snippet=(
            "# Faster model:\n"
            'agent.model = "gpt-4o-mini"\n'
            "agent.timeout_seconds = 30.0  # was 60.0\n"
        ),
        rationale="Smaller models are significantly faster and often sufficient for straightforward queries.",
        priority=60,
        change_type=ChangeType.REASONING_MODEL,
    ),
    # --- Guardrail failures ---
    _Rule(
        FailureMode.GUARDRAIL_VIOLATION,
        "guardrail",
        RecommendationCategory.GUARDRAIL,
        "Adjust guardrail threshold to reduce false positives",
        Impact.HIGH,
        Difficulty.EASY,
        snippet=(
            "# Lower threshold for the triggering filter:\n"
            "# In guardrails.filters config:\n"
            "threshold = 0.6  # was 0.8\n"
            "\n"
            "# Or set fail_open as a safety net:\n"
            "guardrails.fail_open = True\n"
        ),
        rationale="Aggressive guardrails block legitimate outputs; tuning thresholds balances safety and usability.",
        priority=85,
        change_type=ChangeType.GUARDRAIL_THRESHOLD,
    ),
    _Rule(
        FailureMode.GUARDRAIL_VIOLATION,
        "guardrail",
        RecommendationCategory.GUARDRAIL,
        "Add context to the guardrail filter to reduce false positives",
        Impact.MEDIUM,
        Difficulty.MEDIUM,
        snippet=(
            "# Add contextual rules to the guardrail:\n"
            "guardrails.filters.append({\n"
            '    "name": "contextual_allow",\n'
            '    "enabled": True,\n'
            '    "rule": "Allow if the query is clearly informational",\n'
            "})\n"
        ),
        rationale="Context-aware filters can distinguish harmful from benign content more accurately.",
        priority=70,
    ),
    _Rule(
        FailureMode.GUARDRAIL_VIOLATION,
        "guardrail",
        RecommendationCategory.GUARDRAIL,
        "Review and remove overly aggressive filters",
        Impact.MEDIUM,
        Difficulty.EASY,
        snippet=(
            "# Disable the most aggressive filter:\n"
            "# Review guardrail results to identify which filter blocks most\n"
            "# Then disable or raise threshold:\n"
            "guardrails.filters[0].enabled = False\n"
        ),
        rationale="Some filters may be overly broad and block valid responses by design.",
        priority=65,
    ),
    # --- Cost failures ---
    _Rule(
        FailureMode.LOW_SCORE,
        None,
        RecommendationCategory.COST,
        "Use a smaller model for simple queries",
        Impact.MEDIUM,
        Difficulty.MEDIUM,
        snippet=(
            "# Route by query complexity:\n"
            "if query_complexity < 0.5:\n"
            '    agent.model = "gpt-4o-mini"\n'
            "else:\n"
            '    agent.model = "gpt-4o"\n'
        ),
        rationale="Simple queries can be handled by cheaper models without quality loss.",
        priority=55,
    ),
    _Rule(
        FailureMode.TIMEOUT,
        None,
        RecommendationCategory.COST,
        "Cache frequent queries to avoid redundant computation",
        Impact.HIGH,
        Difficulty.MEDIUM,
        snippet=(
            "# Add a query cache layer:\n"
            "from functools import lru_cache\n"
            "\n"
            "@lru_cache(maxsize=1000)\n"
            "def cached_retrieve(query_hash: str, top_k: int):\n"
            "    return vector_store.search(query_hash, top_k)\n"
        ),
        rationale="Caching eliminates repeated expensive operations for common queries.",
        priority=70,
    ),
    _Rule(
        FailureMode.TOKEN_LIMIT,
        None,
        RecommendationCategory.COST,
        "Reduce context window to control token costs",
        Impact.MEDIUM,
        Difficulty.EASY,
        snippet=(
            "# Trim context before reasoning:\n"
            "reranker.top_k = 5  # reduce from 10\n"
            "generator.max_tokens = 2048  # reduce from 4096\n"
        ),
        rationale="Smaller context windows directly reduce token consumption and cost.",
        priority=60,
    ),
]


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------


class RecommendationEngine:
    """Maps blame reports and counterfactual results to actionable fixes."""

    def recommend(
        self,
        blame: BlameReport,
        *,
        counterfactual_delta: float | None = None,
    ) -> RecommendationReport:
        """Generate prioritised recommendations from a blame report.

        Parameters
        ----------
        blame:
            Output of :class:`BlameAttributionEngine.analyse`.
        counterfactual_delta:
            Optional best improvement delta from counterfactual analysis.
            Used to boost/penalise certain recommendations.
        """
        matched: list[Recommendation] = []

        for rule in _RULES:
            if rule.failure_mode != blame.root_cause_mode:
                continue
            if rule.step_name is not None and rule.step_name != blame.root_cause_step:
                continue

            # Base priority from severity
            priority = _priority_for_severity(blame.severity)

            # Combine with rule's own priority
            priority = max(priority, rule.priority)

            # Boost if counterfactual confirms this category helps
            if counterfactual_delta is not None and counterfactual_delta > 0.1:
                if rule.category == RecommendationCategory.RETRIEVAL and "retrieve" in blame.root_cause_step or rule.category == RecommendationCategory.REASONING and "reason" in blame.root_cause_step:
                    priority += 10

            # Cost / latency tradeoff (3.5): estimate the delta introduced by
            # the linked intervention so the UI can show a cost comparison.
            cost_delta = (
                _ESTIMATED_COST_DELTA.get(rule.change_type)  # type: ignore[arg-type]
                if rule.change_type is not None
                else None
            )
            latency_delta = (
                _ESTIMATED_LATENCY_DELTA_MS.get(rule.change_type)  # type: ignore[arg-type]
                if rule.change_type is not None
                else None
            )

            matched.append(
                Recommendation(
                    category=rule.category,
                    action=rule.action,
                    impact=rule.impact,
                    difficulty=rule.difficulty,
                    snippet=rule.snippet,
                    rationale=rule.rationale,
                    priority=priority,
                    change_type=str(rule.change_type) if rule.change_type else None,
                    estimated_cost_delta_usd=cost_delta,
                    estimated_latency_delta_ms=latency_delta,
                )
            )

        # Add general fallback if nothing matched
        if not matched:
            matched.append(
                Recommendation(
                    category=RecommendationCategory.GENERAL,
                    action="Review pipeline logs and configuration for the failing step",
                    impact=Impact.LOW,
                    difficulty=Difficulty.EASY,
                    snippet="# Check logs and step configuration",
                    rationale="No specific rule matched; manual investigation is needed.",
                    priority=10,
                )
            )

        report = RecommendationReport(
            trace_id=blame.run_id,
            recommendations=matched,
        )

        logger.info(
            "recommendations_generated",
            trace_id=blame.run_id,
            root_cause_step=blame.root_cause_step,
            failure_mode=str(blame.root_cause_mode),
            count=len(matched),
        )
        return report
