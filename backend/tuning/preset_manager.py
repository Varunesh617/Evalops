"""Preset configuration management — built-in and user-created presets."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field

from backend.tuning.user_preferences import (
    DomainType,
    OptimizationConstraints,
    OptimizationGoal,
    UserPreferences,
    get_domain_defaults,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Preset model
# ---------------------------------------------------------------------------

class TuningPreset(BaseModel):
    """A saved tuning configuration preset."""

    id: str = Field(default_factory=lambda: f"preset-{uuid.uuid4().hex[:12]}")
    name: str
    description: str = ""
    is_builtin: bool = False
    domain: DomainType = DomainType.GENERAL
    preferences: UserPreferences
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def apply(self) -> UserPreferences:
        """Return a copy of the preset's preferences for use."""
        return self.preferences.model_copy(deep=True)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

def _build_healthcare_preset() -> TuningPreset:
    prefs = get_domain_defaults(DomainType.HEALTHCARE)
    return TuningPreset(
        id="preset-healthcare",
        name="Healthcare",
        description="HIPAA-compliant, high-accuracy preset. Strict guardrails, cost-insensitive.",
        is_builtin=True,
        domain=DomainType.HEALTHCARE,
        preferences=prefs,
    )


def _build_startup_preset() -> TuningPreset:
    prefs = get_domain_defaults(DomainType.GENERAL)
    prefs.optimization.objective = OptimizationGoal.BALANCED
    prefs.optimization.max_trials = 30
    prefs.optimization.max_duration_seconds = 1800.0
    # Enable cost_efficiency, relax guardrails slightly
    for m in prefs.metrics:
        if m.name == "cost_efficiency":
            m.enabled = True
            m.weight = 1.5
    for f in prefs.filters:
        if f.name in ("faithfulness_check", "citation_validator"):
            f.enabled = False
    return TuningPreset(
        id="preset-startup",
        name="Startup",
        description="Balanced cost/quality, fast iteration. Relaxed guardrails for speed.",
        is_builtin=True,
        domain=DomainType.GENERAL,
        preferences=prefs,
    )


def _build_enterprise_preset() -> TuningPreset:
    prefs = get_domain_defaults(DomainType.FINANCE)
    # Extra-strict compliance
    for f in prefs.filters:
        f.enabled = True
        f.threshold = min(f.threshold, 0.4)
    prefs.optimization.objective = OptimizationGoal.QUALITY
    prefs.optimization.constraints = OptimizationConstraints(
        min_quality=0.85,
        max_cost_usd=10.0,
    )
    return TuningPreset(
        id="preset-enterprise",
        name="Enterprise",
        description="Compliance-focused with full audit trail. All filters enabled, strict thresholds.",
        is_builtin=True,
        domain=DomainType.FINANCE,
        preferences=prefs,
    )


def _build_research_preset() -> TuningPreset:
    prefs = get_domain_defaults(DomainType.GENERAL)
    # Max quality everywhere
    for m in prefs.metrics:
        m.enabled = True
        m.weight = 2.0 if m.name in ("faithfulness", "context_relevance") else 1.5
    prefs.optimization.objective = OptimizationGoal.QUALITY
    prefs.optimization.constraints = OptimizationConstraints(min_quality=0.95)
    prefs.optimization.max_trials = 200
    prefs.optimization.max_duration_seconds = 14400.0
    return TuningPreset(
        id="preset-research",
        name="Research",
        description="Maximum quality, unlimited budget. All metrics weighted high, extensive search.",
        is_builtin=True,
        domain=DomainType.GENERAL,
        preferences=prefs,
    )


_BUILTIN_PRESETS: dict[str, TuningPreset] = {}


def _ensure_builtins() -> None:
    if not _BUILTIN_PRESETS:
        for preset in (
            _build_healthcare_preset(),
            _build_startup_preset(),
            _build_enterprise_preset(),
            _build_research_preset(),
        ):
            _BUILTIN_PRESETS[preset.id] = preset


# ---------------------------------------------------------------------------
# Preset manager
# ---------------------------------------------------------------------------

class PresetManager:
    """Manage built-in and user-created tuning presets."""

    def __init__(self) -> None:
        _ensure_builtins()
        self._custom: dict[str, TuningPreset] = {}

    def list_presets(self) -> list[TuningPreset]:
        """Return all available presets (built-in + custom)."""
        all_presets = list(_BUILTIN_PRESETS.values()) + list(self._custom.values())
        return all_presets

    def get_preset(self, preset_id: str) -> TuningPreset | None:
        """Get a preset by ID."""
        return _BUILTIN_PRESETS.get(preset_id) or self._custom.get(preset_id)

    def create_preset(
        self,
        name: str,
        preferences: UserPreferences,
        *,
        description: str = "",
        domain: DomainType = DomainType.GENERAL,
    ) -> TuningPreset:
        """Create a new custom preset."""
        preset = TuningPreset(
            name=name,
            description=description,
            domain=domain,
            preferences=preferences,
        )
        self._custom[preset.id] = preset
        logger.info("preset_created", preset_id=preset.id, name=name)
        return preset

    def save_preset(self, preset: TuningPreset) -> TuningPreset:
        """Update an existing custom preset."""
        if preset.is_builtin:
            raise ValueError(f"Cannot modify built-in preset '{preset.name}'")
        preset.updated_at = datetime.now(UTC)
        self._custom[preset.id] = preset
        logger.info("preset_saved", preset_id=preset.id, name=preset.name)
        return preset

    def delete_preset(self, preset_id: str) -> bool:
        """Delete a custom preset. Built-in presets cannot be deleted."""
        if preset_id in _BUILTIN_PRESETS:
            raise ValueError(f"Cannot delete built-in preset '{preset_id}'")
        removed = self._custom.pop(preset_id, None)
        if removed:
            logger.info("preset_deleted", preset_id=preset_id)
        return removed is not None

    def apply_preset(self, preset_id: str) -> UserPreferences | None:
        """Apply a preset and return its preferences."""
        preset = self.get_preset(preset_id)
        if preset is None:
            return None
        logger.info("preset_applied", preset_id=preset_id, name=preset.name)
        return preset.apply()

    def list_builtin_ids(self) -> list[str]:
        """Return IDs of all built-in presets."""
        return sorted(_BUILTIN_PRESETS.keys())

    def list_custom_ids(self) -> list[str]:
        """Return IDs of all user-created presets."""
        return sorted(self._custom.keys())
