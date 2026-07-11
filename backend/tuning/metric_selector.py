"""Interactive metric selection and weight configuration."""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from backend.eval.metrics import METRIC_REGISTRY
from backend.tuning.user_preferences import MetricPreference

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class MetricInfo(BaseModel):
    """Metadata about an available metric."""

    name: str
    description: str
    enabled: bool = True
    weight: float = 1.0
    category: str = "general"
    impact_hint: str = ""


class MetricSelectionResult(BaseModel):
    """Result of applying metric selections to a sample score."""

    original_scores: dict[str, float]
    weighted_scores: dict[str, float]
    composite_score: float
    enabled_metrics: list[str]


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

_METRIC_CATEGORIES: dict[str, str] = {
    "faithfulness": "quality",
    "context_relevance": "quality",
    "trajectory_coherence": "quality",
    "tool_call_accuracy": "accuracy",
    "guardrail_fp_rate": "safety",
    "cost_efficiency": "efficiency",
}

_IMPACT_HINTS: dict[str, str] = {
    "faithfulness": "Measures factual alignment between context and output. High impact on output correctness.",
    "context_relevance": "Measures retrieval precision. Directly affects answer quality.",
    "trajectory_coherence": "Measures logical flow of the agent trajectory. Affects multi-step reliability.",
    "tool_call_accuracy": "Measures correctness of tool invocations. Critical for agentic pipelines.",
    "guardrail_fp_rate": "Tracks false-positive rate of guardrails. Higher = more legitimate content blocked.",
    "cost_efficiency": "Measures cost per unit quality. Lower cost = higher score.",
}


class MetricSelector:
    """List, enable/disable, weight, and preview evaluation metrics."""

    def list_available(self) -> list[MetricInfo]:
        """Return all registered metrics with metadata."""
        infos: list[MetricInfo] = []
        for name, cls in METRIC_REGISTRY.items():
            instance = cls()
            infos.append(
                MetricInfo(
                    name=name,
                    description=instance.description or name.replace("_", " ").title(),
                    category=_METRIC_CATEGORIES.get(name, "general"),
                    impact_hint=_IMPACT_HINTS.get(name, ""),
                )
            )
        return infos

    def get_metric_descriptions(self) -> dict[str, str]:
        """Return a name→description mapping for all metrics."""
        result: dict[str, str] = {}
        for name, cls in METRIC_REGISTRY.items():
            instance = cls()
            result[name] = instance.description or name.replace("_", " ").title()
        return result

    def apply_preferences(
        self,
        preferences: list[MetricPreference],
        sample_scores: dict[str, float] | None = None,
    ) -> list[MetricInfo]:
        """Update MetricInfo list from user preferences."""
        info_map = {i.name: i for i in self.list_available()}
        for pref in preferences:
            if pref.name in info_map:
                info_map[pref.name].enabled = pref.enabled
                info_map[pref.name].weight = pref.weight
        return list(info_map.values())

    def preview_scores(
        self,
        preferences: list[MetricPreference],
        sample_scores: dict[str, float],
    ) -> MetricSelectionResult:
        """Compute weighted composite score from raw metric scores and preferences."""
        pref_map = {p.name: p for p in preferences}
        enabled_metrics: list[str] = []
        weighted_scores: dict[str, float] = {}

        total_weight = 0.0
        total_weighted = 0.0

        for name, score in sample_scores.items():
            pref = pref_map.get(name)
            if pref and pref.enabled:
                enabled_metrics.append(name)
                weighted = score * pref.weight
                weighted_scores[name] = round(weighted, 4)
                total_weight += pref.weight
                total_weighted += weighted

        composite = round(total_weighted / total_weight, 4) if total_weight else 0.0

        return MetricSelectionResult(
            original_scores=sample_scores,
            weighted_scores=weighted_scores,
            composite_score=composite,
            enabled_metrics=enabled_metrics,
        )

    def validate_preferences(self, preferences: list[MetricPreference]) -> list[str]:
        """Validate metric preferences against the registry. Returns warnings."""
        warnings: list[str] = []
        available = set(METRIC_REGISTRY.keys())
        for pref in preferences:
            if pref.name not in available:
                warnings.append(f"Unknown metric '{pref.name}'. Available: {sorted(available)}")
            if pref.weight <= 0:
                warnings.append(f"Metric '{pref.name}' has non-positive weight ({pref.weight}).")
        return warnings
