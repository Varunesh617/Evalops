"""Guardrail filter configuration and preview."""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, Field

from backend.guardrails.filters import (
    BaseFilter,
    CitationValidator,
    FaithfulnessFilter,
    PIIFilter,
    PromptInjectionFilter,
    ToxicityFilter,
)
from backend.tuning.user_preferences import FilterPreference

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Registry of available filter classes
# ---------------------------------------------------------------------------

_FILTER_REGISTRY: dict[str, type[BaseFilter]] = {
    "prompt_injection": PromptInjectionFilter,
    "pii": PIIFilter,
    "toxicity": ToxicityFilter,
    "faithfulness_check": FaithfulnessFilter,
    "citation_validator": CitationValidator,
}

_FILTER_DESCRIPTIONS: dict[str, str] = {
    "prompt_injection": "Detects and blocks prompt injection attempts in user input.",
    "pii": "Identifies and redacts personally identifiable information (SSN, email, phone, etc.).",
    "toxicity": "Filters toxic, offensive, or harmful content.",
    "faithfulness_check": "Validates that generated output is faithful to retrieved context.",
    "citation_validator": "Ensures citations in output reference actual source documents.",
}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class FilterInfo(BaseModel):
    """Metadata about an available guardrail filter."""

    name: str
    description: str
    enabled: bool = True
    threshold: float = 0.5
    priority: int = 0
    category: str = "safety"
    block_rate: float = 0.0
    false_positive_rate: float = 0.0
    avg_duration_ms: float = 0.0


class FilterPreviewResult(BaseModel):
    """Result of previewing filter impact on sample data."""

    sample_inputs: list[str]
    results: list[dict[str, Any]]
    total_blocked: int
    total_allowed: int
    filters_applied: list[str]


# ---------------------------------------------------------------------------
# Configurator
# ---------------------------------------------------------------------------

class FilterConfigurator:
    """List, configure, and preview guardrail filter settings."""

    def list_available(self) -> list[FilterInfo]:
        """Return all registered filters with metadata."""
        infos: list[FilterInfo] = []
        for name, cls in _FILTER_REGISTRY.items():
            instance = cls()
            metrics = instance.get_metrics()
            infos.append(
                FilterInfo(
                    name=name,
                    description=_FILTER_DESCRIPTIONS.get(name, name.replace("_", " ").title()),
                    enabled=instance.enabled,
                    threshold=instance.threshold,
                    block_rate=metrics.get("block_rate", 0.0),
                    false_positive_rate=metrics.get("false_positive_rate", 0.0),
                    avg_duration_ms=metrics.get("avg_duration_ms", 0.0),
                )
            )
        return infos

    def apply_preferences(self, preferences: list[FilterPreference]) -> list[FilterInfo]:
        """Update FilterInfo list from user preferences."""
        info_map = {i.name: i for i in self.list_available()}
        for pref in preferences:
            if pref.name in info_map:
                info_map[pref.name].enabled = pref.enabled
                info_map[pref.name].threshold = pref.threshold
                info_map[pref.name].priority = pref.priority
        result = list(info_map.values())
        result.sort(key=lambda f: f.priority, reverse=True)
        return result

    def build_filter_instances(
        self,
        preferences: list[FilterPreference],
    ) -> list[BaseFilter]:
        """Instantiate filter objects from preferences."""
        pref_map = {p.name: p for p in preferences}
        instances: list[BaseFilter] = []
        for name, cls in _FILTER_REGISTRY.items():
            pref = pref_map.get(name)
            enabled = pref.enabled if pref else True
            threshold = pref.threshold if pref else 0.5
            instances.append(cls(enabled=enabled, threshold=threshold))
        return instances

    def preview(
        self,
        preferences: list[FilterPreference],
        sample_inputs: list[str],
    ) -> FilterPreviewResult:
        """Run filters on sample inputs and report results."""
        instances = self.build_filter_instances(preferences)
        applied_names = [f.name for f in instances if f.enabled]
        results: list[dict[str, Any]] = []
        total_blocked = 0
        total_allowed = 0

        for text in sample_inputs:
            input_results: list[dict[str, Any]] = []
            for f in instances:
                result = f.check(text)
                input_results.append({
                    "filter": result.filter_name,
                    "decision": result.decision.value,
                    "score": round(result.score, 3),
                    "risk_level": result.risk_level.value,
                    "duration_ms": round(result.duration_ms, 2),
                })
                if result.blocked:
                    total_blocked += 1
                else:
                    total_allowed += 1
            results.append({"input": text[:200], "filter_results": input_results})

        return FilterPreviewResult(
            sample_inputs=sample_inputs,
            results=results,
            total_blocked=total_blocked,
            total_allowed=total_allowed,
            filters_applied=applied_names,
        )

    def validate_preferences(self, preferences: list[FilterPreference]) -> list[str]:
        """Validate filter preferences. Returns warnings."""
        warnings: list[str] = []
        available = set(_FILTER_REGISTRY.keys())
        for pref in preferences:
            if pref.name not in available:
                warnings.append(f"Unknown filter '{pref.name}'. Available: {sorted(available)}")
            if not 0.0 <= pref.threshold <= 1.0:
                warnings.append(f"Filter '{pref.name}' threshold {pref.threshold} out of range [0, 1].")
        return warnings

    @staticmethod
    def get_available_filter_names() -> list[str]:
        """Return sorted list of registered filter names."""
        return sorted(_FILTER_REGISTRY.keys())
