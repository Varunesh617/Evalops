"""User preference management for the tuning system."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain presets
# ---------------------------------------------------------------------------

class DomainType(StrEnum):
    HEALTHCARE = "healthcare"
    FINANCE = "finance"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Preference models
# ---------------------------------------------------------------------------

class MetricPreference(BaseModel):
    """Configuration for a single metric in user preferences."""

    name: str
    enabled: bool = True
    weight: float = Field(default=1.0, ge=0.0, le=10.0)


class FilterPreference(BaseModel):
    """Configuration for a single filter in user preferences."""

    name: str
    enabled: bool = True
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    priority: int = Field(default=0, ge=0, le=100)


class OptimizationGoal(StrEnum):
    COST = "cost"
    QUALITY = "quality"
    LATENCY = "latency"
    BALANCED = "balanced"


class OptimizationConstraints(BaseModel):
    """Hard constraints for optimization runs."""

    max_cost_usd: float | None = Field(default=None, ge=0.0)
    min_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    max_latency_ms: float | None = Field(default=None, ge=0.0)


class OptimizationPreferences(BaseModel):
    """User's optimization goal configuration."""

    objective: OptimizationGoal = OptimizationGoal.BALANCED
    constraints: OptimizationConstraints = Field(default_factory=OptimizationConstraints)
    max_trials: int = Field(default=50, ge=1, le=500)
    max_duration_seconds: float = Field(default=3600.0, ge=60.0, le=86400.0)


class UserPreferences(BaseModel):
    """Complete user preference set for pipeline tuning."""

    id: str = Field(default_factory=lambda: f"pref-{uuid.uuid4().hex[:12]}")
    user_id: str = "default"
    domain: DomainType = DomainType.GENERAL

    metrics: list[MetricPreference] = Field(default_factory=list)
    filters: list[FilterPreference] = Field(default_factory=list)
    optimization: OptimizationPreferences = Field(default_factory=OptimizationPreferences)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_yaml(self) -> str:
        """Export preferences as YAML."""
        return yaml.dump(
            self.model_dump(mode="json"),
            default_flow_style=False,
            sort_keys=False,
        )

    def to_json(self) -> str:
        """Export preferences as formatted JSON."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_yaml(cls, text: str) -> UserPreferences:
        """Import preferences from YAML."""
        data = yaml.safe_load(text)
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, text: str) -> UserPreferences:
        """Import preferences from JSON."""
        data = json.loads(text)
        return cls.model_validate(data)


# ---------------------------------------------------------------------------
# Default preferences per domain
# ---------------------------------------------------------------------------

_DOMAIN_DEFAULTS: dict[DomainType, dict[str, Any]] = {
    DomainType.HEALTHCARE: {
        "domain": "healthcare",
        "metrics": [
            {"name": "faithfulness", "enabled": True, "weight": 2.0},
            {"name": "context_relevance", "enabled": True, "weight": 1.5},
            {"name": "trajectory_coherence", "enabled": True, "weight": 1.0},
            {"name": "tool_call_accuracy", "enabled": True, "weight": 1.0},
            {"name": "guardrail_fp_rate", "enabled": True, "weight": 1.5},
            {"name": "cost_efficiency", "enabled": False, "weight": 0.5},
        ],
        "filters": [
            {"name": "prompt_injection", "enabled": True, "threshold": 0.3, "priority": 100},
            {"name": "pii", "enabled": True, "threshold": 0.4, "priority": 90},
            {"name": "toxicity", "enabled": True, "threshold": 0.3, "priority": 80},
            {"name": "faithfulness_check", "enabled": True, "threshold": 0.5, "priority": 70},
            {"name": "citation_validator", "enabled": True, "threshold": 0.5, "priority": 60},
        ],
        "optimization": {
            "objective": "quality",
            "constraints": {"min_quality": 0.9, "max_latency_ms": 10000.0},
            "max_trials": 100,
            "max_duration_seconds": 7200.0,
        },
    },
    DomainType.FINANCE: {
        "domain": "finance",
        "metrics": [
            {"name": "faithfulness", "enabled": True, "weight": 1.5},
            {"name": "context_relevance", "enabled": True, "weight": 1.5},
            {"name": "trajectory_coherence", "enabled": True, "weight": 1.0},
            {"name": "tool_call_accuracy", "enabled": True, "weight": 1.5},
            {"name": "guardrail_fp_rate", "enabled": True, "weight": 1.0},
            {"name": "cost_efficiency", "enabled": True, "weight": 1.0},
        ],
        "filters": [
            {"name": "prompt_injection", "enabled": True, "threshold": 0.4, "priority": 100},
            {"name": "pii", "enabled": True, "threshold": 0.5, "priority": 90},
            {"name": "toxicity", "enabled": True, "threshold": 0.5, "priority": 70},
            {"name": "faithfulness_check", "enabled": True, "threshold": 0.6, "priority": 80},
            {"name": "citation_validator", "enabled": True, "threshold": 0.4, "priority": 60},
        ],
        "optimization": {
            "objective": "balanced",
            "constraints": {"min_quality": 0.8, "max_cost_usd": 2.0},
            "max_trials": 75,
            "max_duration_seconds": 5400.0,
        },
    },
    DomainType.GENERAL: {
        "domain": "general",
        "metrics": [
            {"name": "faithfulness", "enabled": True, "weight": 1.0},
            {"name": "context_relevance", "enabled": True, "weight": 1.0},
            {"name": "trajectory_coherence", "enabled": True, "weight": 1.0},
            {"name": "tool_call_accuracy", "enabled": True, "weight": 1.0},
            {"name": "guardrail_fp_rate", "enabled": False, "weight": 0.5},
            {"name": "cost_efficiency", "enabled": True, "weight": 1.0},
        ],
        "filters": [
            {"name": "prompt_injection", "enabled": True, "threshold": 0.5, "priority": 100},
            {"name": "pii", "enabled": True, "threshold": 0.6, "priority": 90},
            {"name": "toxicity", "enabled": True, "threshold": 0.5, "priority": 80},
            {"name": "faithfulness_check", "enabled": False, "threshold": 0.5, "priority": 50},
            {"name": "citation_validator", "enabled": False, "threshold": 0.5, "priority": 40},
        ],
        "optimization": {
            "objective": "balanced",
            "constraints": {},
            "max_trials": 50,
            "max_duration_seconds": 3600.0,
        },
    },
}


def get_domain_defaults(domain: DomainType) -> UserPreferences:
    """Return a new UserPreferences instance seeded with domain defaults."""
    data = _DOMAIN_DEFAULTS[domain]
    return UserPreferences.model_validate(data)


# ---------------------------------------------------------------------------
# Preferences manager (in-memory, swap for DB in Phase 6)
# ---------------------------------------------------------------------------

class UserPreferencesManager:
    """Load/save/query user preferences.

    Uses an in-memory store. Replace with a DB backend when available.
    """

    def __init__(self) -> None:
        self._store: dict[str, UserPreferences] = {}

    def get(self, user_id: str = "default") -> UserPreferences | None:
        """Get preferences for a user."""
        return self._store.get(user_id)

    def get_or_create(self, user_id: str = "default", domain: DomainType = DomainType.GENERAL) -> UserPreferences:
        """Get existing preferences or create from domain defaults."""
        existing = self._store.get(user_id)
        if existing is not None:
            return existing
        prefs = get_domain_defaults(domain)
        prefs.user_id = user_id
        self._store[user_id] = prefs
        logger.info("preferences_created", user_id=user_id, domain=domain.value)
        return prefs

    def save(self, preferences: UserPreferences) -> UserPreferences:
        """Save (create or update) user preferences."""
        preferences.updated_at = datetime.now(UTC)
        self._store[preferences.user_id] = preferences
        logger.info("preferences_saved", user_id=preferences.user_id, pref_id=preferences.id)
        return preferences

    def delete(self, user_id: str) -> bool:
        """Delete preferences for a user."""
        removed = self._store.pop(user_id, None)
        if removed:
            logger.info("preferences_deleted", user_id=user_id)
        return removed is not None

    def list_all(self) -> list[UserPreferences]:
        """Return all stored preferences."""
        return list(self._store.values())
