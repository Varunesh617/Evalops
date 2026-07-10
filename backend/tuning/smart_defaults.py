"""AI-powered smart defaults — analyze patterns and suggest optimal configurations."""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, Field

from backend.tuning.user_preferences import (
    DomainType,
    FilterPreference,
    MetricPreference,
    OptimizationConstraints,
    OptimizationGoal,
    OptimizationPreferences,
    UserPreferences,
    get_domain_defaults,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MetricSuggestion(BaseModel):
    """Suggested metric configuration."""

    name: str
    recommended_weight: float
    enabled: bool
    rationale: str


class FilterSuggestion(BaseModel):
    """Suggested filter configuration."""

    name: str
    recommended_threshold: float
    enabled: bool
    rationale: str


class OptimizationSuggestion(BaseModel):
    """Suggested optimization goal."""

    objective: OptimizationGoal
    rationale: str
    suggested_constraints: OptimizationConstraints


class SmartDefaultsResult(BaseModel):
    """Complete set of AI-powered recommendations."""

    domain: DomainType
    metric_suggestions: list[MetricSuggestion]
    filter_suggestions: list[FilterSuggestion]
    optimization_suggestion: OptimizationSuggestion
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


# ---------------------------------------------------------------------------
# Pipeline usage analysis (stub — plug in real telemetry in Phase 6)
# ---------------------------------------------------------------------------

class PipelineUsageStats:
    """Aggregated usage statistics from pipeline runs.

    In production, this pulls from the trace store and eval results.
    """

    def __init__(
        self,
        *,
        avg_cost_usd: float = 0.5,
        avg_latency_ms: float = 2000.0,
        avg_quality: float = 0.75,
        total_runs: int = 0,
        common_failures: list[str] | None = None,
        domain: DomainType = DomainType.GENERAL,
    ) -> None:
        self.avg_cost_usd = avg_cost_usd
        self.avg_latency_ms = avg_latency_ms
        self.avg_quality = avg_quality
        self.total_runs = total_runs
        self.common_failures = common_failures or []
        self.domain = domain


# ---------------------------------------------------------------------------
# Smart defaults engine
# ---------------------------------------------------------------------------

class SmartDefaults:
    """Analyze pipeline usage and recommend optimal tuning configurations."""

    def __init__(self, usage_stats: PipelineUsageStats | None = None) -> None:
        self._stats = usage_stats or PipelineUsageStats()

    def generate(self) -> SmartDefaultsResult:
        """Generate a full set of smart default recommendations."""
        domain = self._stats.domain
        base_prefs = get_domain_defaults(domain)

        metric_suggestions = self._suggest_metrics(base_prefs.metrics)
        filter_suggestions = self._suggest_filters(base_prefs.filters)
        optimization_suggestion = self._suggest_optimization()
        confidence = self._compute_confidence()

        reasoning_parts: list[str] = []
        if self._stats.total_runs > 100:
            reasoning_parts.append("High confidence from large run history.")
        elif self._stats.total_runs > 10:
            reasoning_parts.append("Moderate confidence from limited run history.")
        else:
            reasoning_parts.append("Low confidence — using domain defaults with adjustments.")

        if self._stats.avg_cost_usd > 2.0:
            reasoning_parts.append("High average cost detected; favoring cost efficiency.")
        if self._stats.avg_latency_ms > 5000:
            reasoning_parts.append("High latency detected; suggest tighter constraints.")

        return SmartDefaultsResult(
            domain=domain,
            metric_suggestions=metric_suggestions,
            filter_suggestions=filter_suggestions,
            optimization_suggestion=optimization_suggestion,
            confidence=confidence,
            reasoning=" ".join(reasoning_parts),
        )

    def _suggest_metrics(self, defaults: list[MetricPreference]) -> list[MetricSuggestion]:
        """Adjust metric weights based on usage patterns."""
        suggestions: list[MetricSuggestion] = []
        for m in defaults:
            weight = m.weight
            enabled = m.enabled
            rationale = "Domain default"

            if m.name == "cost_efficiency":
                if self._stats.avg_cost_usd > 1.5:
                    weight = min(weight * 1.5, 10.0)
                    enabled = True
                    rationale = f"Average cost ${self._stats.avg_cost_usd:.2f} is above threshold; increasing cost weight."
                elif self._stats.avg_cost_usd < 0.2:
                    enabled = False
                    rationale = f"Average cost ${self._stats.avg_cost_usd:.2f} is low; cost metric not critical."

            elif m.name == "faithfulness":
                if self._stats.avg_quality < 0.7:
                    weight = min(weight * 1.3, 10.0)
                    rationale = f"Quality {self._stats.avg_quality:.2f} is below target; boosting faithfulness."

            elif m.name == "guardrail_fp_rate":
                if any("false_positive" in f for f in self._stats.common_failures):
                    weight = min(weight * 2.0, 10.0)
                    enabled = True
                    rationale = "False-positive failures detected in history; monitoring FP rate."

            suggestions.append(MetricSuggestion(
                name=m.name,
                recommended_weight=round(weight, 2),
                enabled=enabled,
                rationale=rationale,
            ))
        return suggestions

    def _suggest_filters(self, defaults: list[FilterPreference]) -> list[FilterSuggestion]:
        """Adjust filter thresholds based on failure patterns."""
        suggestions: list[FilterSuggestion] = []
        for f in defaults:
            threshold = f.threshold
            enabled = f.enabled
            rationale = "Domain default"

            if f.name == "prompt_injection":
                if self._stats.domain in (DomainType.HEALTHCARE, DomainType.FINANCE):
                    threshold = min(threshold, 0.35)
                    rationale = f"Strict mode for {self._stats.domain.value} domain."

            elif f.name == "pii":
                if self._stats.domain == DomainType.HEALTHCARE:
                    threshold = min(threshold, 0.35)
                    rationale = "HIPAA compliance requires aggressive PII detection."

            elif f.name in ("faithfulness_check", "citation_validator"):
                if self._stats.avg_quality < 0.6:
                    enabled = True
                    threshold = max(threshold, 0.6)
                    rationale = f"Quality {self._stats.avg_quality:.2f} is low; enabling output validation."

            suggestions.append(FilterSuggestion(
                name=f.name,
                recommended_threshold=round(threshold, 2),
                enabled=enabled,
                rationale=rationale,
            ))
        return suggestions

    def _suggest_optimization(self) -> OptimizationSuggestion:
        """Recommend optimization objective based on cost/quality patterns."""
        stats = self._stats

        if stats.avg_cost_usd > 2.0:
            objective = OptimizationGoal.COST
            rationale = f"Average cost ${stats.avg_cost_usd:.2f} is high; prioritize cost reduction."
            constraints = OptimizationConstraints(max_cost_usd=1.0)
        elif stats.avg_latency_ms > 5000:
            objective = OptimizationGoal.LATENCY
            rationale = f"Average latency {stats.avg_latency_ms:.0f}ms is high; prioritize speed."
            constraints = OptimizationConstraints(max_latency_ms=3000.0)
        elif stats.avg_quality < 0.7:
            objective = OptimizationGoal.QUALITY
            rationale = f"Quality {stats.avg_quality:.2f} is below target; prioritize accuracy."
            constraints = OptimizationConstraints(min_quality=0.85)
        else:
            objective = OptimizationGoal.BALANCED
            rationale = "Metrics are within healthy ranges; maintain balanced optimization."
            constraints = OptimizationConstraints()

        return OptimizationSuggestion(
            objective=objective,
            rationale=rationale,
            suggested_constraints=constraints,
        )

    def _compute_confidence(self) -> float:
        """Compute confidence score based on available data."""
        runs = self._stats.total_runs
        if runs >= 500:
            return 0.95
        if runs >= 100:
            return 0.80
        if runs >= 50:
            return 0.65
        if runs >= 10:
            return 0.45
        return 0.25
