"""Optimization goal configuration and search-space management."""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, Field

from backend.optimizer.config_sweeper import compute_composite_score
from backend.tuning.user_preferences import (
    OptimizationConstraints,
    OptimizationGoal,
    OptimizationPreferences,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ObjectiveWeights(BaseModel):
    """Computed weights derived from the chosen optimization objective."""

    quality_weight: float = 0.6
    cost_weight: float = 0.25
    latency_weight: float = 0.15


class SearchSpaceConfig(BaseModel):
    """Parameters that control the Optuna search space."""

    retrieval_strategies: list[str] = Field(
        default_factory=lambda: ["dense", "sparse", "hybrid"],
    )
    agent_models: list[str] = Field(
        default_factory=lambda: ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-5-haiku"],
    )
    generator_models: list[str] = Field(
        default_factory=lambda: ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-5-haiku"],
    )
    reranker_models: list[str] = Field(
        default_factory=lambda: ["cross_encoder", "cohere", "custom"],
    )
    retrieval_top_k_range: tuple[int, int] = (5, 100)
    reranker_top_k_range: tuple[int, int] = (3, 50)
    agent_max_tokens_range: tuple[int, int] = (512, 8192)
    agent_temperature_range: tuple[float, float] = (0.0, 1.5)
    generator_temperature_range: tuple[float, float] = (0.0, 1.0)
    guardrail_filter_names: list[str] = Field(
        default_factory=lambda: [
            "prompt_injection", "pii", "toxicity",
            "faithfulness_check", "citation_validator",
        ],
    )


class OptimizationPreview(BaseModel):
    """Preview of expected optimization outcomes."""

    objective: OptimizationGoal
    weights: ObjectiveWeights
    constraints: OptimizationConstraints
    max_trials: int
    max_duration_seconds: float
    search_space: SearchSpaceConfig
    estimated_params: int
    estimated_cost_range_usd: tuple[float, float]


# ---------------------------------------------------------------------------
# Configurator
# ---------------------------------------------------------------------------

_OBJECTIVE_WEIGHTS: dict[OptimizationGoal, ObjectiveWeights] = {
    OptimizationGoal.QUALITY: ObjectiveWeights(quality_weight=0.85, cost_weight=0.05, latency_weight=0.10),
    OptimizationGoal.COST: ObjectiveWeights(quality_weight=0.20, cost_weight=0.70, latency_weight=0.10),
    OptimizationGoal.LATENCY: ObjectiveWeights(quality_weight=0.20, cost_weight=0.10, latency_weight=0.70),
    OptimizationGoal.BALANCED: ObjectiveWeights(quality_weight=0.60, cost_weight=0.25, latency_weight=0.15),
}


class OptimizationConfigurator:
    """Configure optimization goals, constraints, search space, and budget."""

    def __init__(self, search_space: SearchSpaceConfig | None = None) -> None:
        self._search_space = search_space or SearchSpaceConfig()

    def get_weights(self, objective: OptimizationGoal) -> ObjectiveWeights:
        """Return composite score weights for the given objective."""
        return _OBJECTIVE_WEIGHTS[objective]

    def get_current_config(self, preferences: OptimizationPreferences) -> dict[str, Any]:
        """Return the full optimization config as a dict."""
        weights = self.get_weights(preferences.objective)
        return {
            "objective": preferences.objective.value,
            "weights": weights.model_dump(),
            "constraints": preferences.constraints.model_dump(),
            "max_trials": preferences.max_trials,
            "max_duration_seconds": preferences.max_duration_seconds,
        }

    def update_preferences(
        self,
        preferences: OptimizationPreferences,
        *,
        objective: OptimizationGoal | None = None,
        constraints: OptimizationConstraints | None = None,
        max_trials: int | None = None,
        max_duration_seconds: float | None = None,
    ) -> OptimizationPreferences:
        """Update optimization preferences selectively."""
        if objective is not None:
            preferences.objective = objective
        if constraints is not None:
            preferences.constraints = constraints
        if max_trials is not None:
            preferences.max_trials = max_trials
        if max_duration_seconds is not None:
            preferences.max_duration_seconds = max_duration_seconds
        logger.info(
            "optimization_preferences_updated",
            objective=preferences.objective.value,
            max_trials=preferences.max_trials,
        )
        return preferences

    def preview(self, preferences: OptimizationPreferences) -> OptimizationPreview:
        """Generate a preview of the optimization run configuration."""
        weights = self.get_weights(preferences.objective)
        space = self._search_space

        # Estimate distinct parameter count
        param_count = (
            len(space.retrieval_strategies)
            + len(space.agent_models)
            + len(space.generator_models)
            + len(space.reranker_models)
            + 7  # numeric ranges
            + len(space.guardrail_filter_names) * 2  # enabled + threshold per filter
        )

        # Rough cost estimate: $0.01-$0.10 per trial
        estimated_cost = (
            round(0.01 * preferences.max_trials, 2),
            round(0.10 * preferences.max_trials, 2),
        )

        return OptimizationPreview(
            objective=preferences.objective,
            weights=weights,
            constraints=preferences.constraints,
            max_trials=preferences.max_trials,
            max_duration_seconds=preferences.max_duration_seconds,
            search_space=space,
            estimated_params=param_count,
            estimated_cost_range_usd=estimated_cost,
        )

    def estimate_composite(
        self,
        quality: float,
        cost_usd: float,
        latency_ms: float,
        objective: OptimizationGoal,
        *,
        max_cost_usd: float = 5.0,
        max_latency_ms: float = 30_000.0,
    ) -> float:
        """Quick composite score estimate without running optimization."""
        weights = self.get_weights(objective)
        return compute_composite_score(
            quality=quality,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            quality_weight=weights.quality_weight,
            cost_weight=weights.cost_weight,
            latency_weight=weights.latency_weight,
            max_cost_usd=max_cost_usd,
            max_latency_ms=max_latency_ms,
        )
