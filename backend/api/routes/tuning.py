"""Tuning API routes — preferences, metrics, filters, optimization, presets, smart defaults."""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from backend.db.repository import TuningPresetRepository, UserPreferenceRepository
from backend.db.session import get_session_factory
from backend.tuning.config_schema import ConfigSchemaGenerator
from backend.tuning.filter_configurator import FilterConfigurator
from backend.tuning.metric_selector import MetricSelector
from backend.tuning.optimization_config import OptimizationConfigurator
from backend.tuning.preset_manager import PresetManager
from backend.tuning.smart_defaults import PipelineUsageStats, SmartDefaults
from backend.tuning.user_preferences import (
    DomainType,
    FilterPreference,
    MetricPreference,
    OptimizationConstraints,
    OptimizationGoal,
    UserPreferences,
    UserPreferencesManager,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tuning", tags=["tuning"])

# ---------------------------------------------------------------------------
# Singleton managers (swap for DI in Phase 6)
# ---------------------------------------------------------------------------

_pref_manager = UserPreferencesManager()
_metric_selector = MetricSelector()
_filter_configurator = FilterConfigurator()
_opt_configurator = OptimizationConfigurator()
_preset_manager = PresetManager()
_schema_gen = ConfigSchemaGenerator()

# Wire optional DB persistence when DATABASE_URL is configured.
_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    try:
        _factory = get_session_factory(_database_url)
        _preset_manager.set_db_repos(
            TuningPresetRepository(_factory()),
            UserPreferenceRepository(_factory()),
        )
    except Exception as exc:
        logger.warning("tuning_db_init_failed", error=str(exc))


async def _persist_prefs(prefs: UserPreferences) -> None:
    """Persist user preferences to the DB if a repository is wired."""
    repo = _preset_manager._pref_repo  # type: ignore[attr-defined]
    if repo is None:
        return
    try:
        await repo.upsert(prefs.user_id, preferences_json=prefs.model_dump(mode="json"))
    except Exception as exc:
        logger.warning("preferences_persist_failed", user_id=prefs.user_id, error=str(exc))


async def _load_prefs(user_id: str) -> UserPreferences | None:
    """Load user preferences from the DB if a repository is wired."""
    repo = _preset_manager._pref_repo  # type: ignore[attr-defined]
    if repo is None:
        return None
    try:
        row = await repo.get_for_user(user_id)
    except Exception as exc:
        logger.warning("preferences_load_failed", user_id=user_id, error=str(exc))
        return None
    if row is None:
        return None
    try:
        return UserPreferences.model_validate(row.get("preferences_json", {}))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

@router.get("/preferences")
async def get_preferences(user_id: str = "default", domain: str = "general") -> dict[str, Any]:
    """Get user preferences, creating defaults if needed."""
    try:
        domain_enum = DomainType(domain)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid domain: {domain}. Valid: {[d.value for d in DomainType]}",
        )
    # Prefer a DB-backed value when available; otherwise fall back to in-memory.
    db_prefs = await _load_prefs(user_id)
    if db_prefs is not None:
        _pref_manager.save(db_prefs)
        return db_prefs.model_dump(mode="json")
    prefs = _pref_manager.get_or_create(user_id, domain_enum)
    return prefs.model_dump(mode="json")


@router.put("/preferences")
async def update_preferences(body: UserPreferences) -> dict[str, Any]:
    """Update user preferences."""
    saved = _pref_manager.save(body)
    await _persist_prefs(saved)
    logger.info("tuning_preferences_updated", user_id=saved.user_id)
    return saved.model_dump(mode="json")


@router.get("/preferences/export")
async def export_preferences(
    user_id: str = "default",
    format: str = "yaml",
) -> dict[str, str]:
    """Export user preferences as YAML or JSON."""
    prefs = await _load_prefs(user_id) or _pref_manager.get(user_id)
    if prefs is None:
        raise HTTPException(status_code=404, detail="No preferences found for this user.")
    if format == "json":
        return {"content": prefs.to_json(), "format": "json"}
    return {"content": prefs.to_yaml(), "format": "yaml"}


@router.put("/preferences/import")
async def import_preferences(body: dict[str, Any]) -> dict[str, Any]:
    """Import user preferences from YAML or JSON content."""
    fmt = body.get("format", "json")
    content = body.get("content", "")
    user_id = body.get("user_id", "default")
    if fmt == "yaml":
        prefs = UserPreferences.from_yaml(content)
    else:
        prefs = UserPreferences.from_json(content)
    prefs.user_id = user_id
    saved = _pref_manager.save(prefs)
    await _persist_prefs(saved)
    return saved.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def list_metrics(user_id: str = "default") -> list[dict[str, Any]]:
    """List available metrics with user preference overrides."""
    prefs = _pref_manager.get(user_id)
    metric_prefs = prefs.metrics if prefs else []
    infos = _metric_selector.apply_preferences(metric_prefs)
    return [i.model_dump() for i in infos]


@router.put("/metrics")
async def configure_metrics(
    user_id: str = "default",
    metrics: list[MetricPreference] | None = None,
) -> dict[str, Any]:
    """Update metric selection and weights."""
    if metrics is None:
        raise HTTPException(status_code=422, detail="metrics list is required.")

    warnings = _metric_selector.validate_preferences(metrics)
    if warnings:
        logger.warning("metric_validation_warnings", warnings=warnings)

    prefs = _pref_manager.get_or_create(user_id)
    prefs.metrics = metrics
    _pref_manager.save(prefs)

    return {
        "preferences": prefs.model_dump(mode="json"),
        "warnings": warnings,
    }


@router.post("/metrics/preview")
async def preview_metrics(
    preferences: list[MetricPreference],
    sample_scores: dict[str, float],
) -> dict[str, Any]:
    """Preview how metric selections affect composite scores."""
    result = _metric_selector.preview_scores(preferences, sample_scores)
    return result.model_dump()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

@router.get("/filters")
async def list_filters(user_id: str = "default") -> list[dict[str, Any]]:
    """List available filters with statistics."""
    prefs = _pref_manager.get(user_id)
    filter_prefs = prefs.filters if prefs else []
    infos = _filter_configurator.apply_preferences(filter_prefs)
    return [i.model_dump() for i in infos]


@router.put("/filters")
async def configure_filters(
    user_id: str = "default",
    filters: list[FilterPreference] | None = None,
) -> dict[str, Any]:
    """Update filter selection, thresholds, and priority."""
    if filters is None:
        raise HTTPException(status_code=422, detail="filters list is required.")

    warnings = _filter_configurator.validate_preferences(filters)
    if warnings:
        logger.warning("filter_validation_warnings", warnings=warnings)

    prefs = _pref_manager.get_or_create(user_id)
    prefs.filters = filters
    _pref_manager.save(prefs)

    return {
        "preferences": prefs.model_dump(mode="json"),
        "warnings": warnings,
    }


@router.post("/filters/preview")
async def preview_filters(
    filters: list[FilterPreference],
    sample_inputs: list[str],
) -> dict[str, Any]:
    """Preview filter impact on sample data."""
    result = _filter_configurator.preview(filters, sample_inputs)
    return result.model_dump()


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------

@router.get("/optimization")
async def get_optimization_config(user_id: str = "default") -> dict[str, Any]:
    """Get the current optimization configuration."""
    prefs = _pref_manager.get_or_create(user_id)
    return _opt_configurator.get_current_config(prefs.optimization)


@router.put("/optimization")
async def update_optimization_config(
    user_id: str = "default",
    objective: str | None = None,
    constraints: OptimizationConstraints | None = None,
    max_trials: int | None = None,
    max_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Update optimization goals and constraints."""
    prefs = _pref_manager.get_or_create(user_id)

    obj_enum = None
    if objective:
        try:
            obj_enum = OptimizationGoal(objective)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid objective: {objective}. Valid: {[g.value for g in OptimizationGoal]}",
            )
    prefs.optimization = _opt_configurator.update_preferences(
        prefs.optimization,
        objective=obj_enum,
        constraints=constraints,
        max_trials=max_trials,
        max_duration_seconds=max_duration_seconds,
    )
    _pref_manager.save(prefs)

    return _opt_configurator.get_current_config(prefs.optimization)


@router.post("/optimization/preview")
async def preview_optimization(user_id: str = "default") -> dict[str, Any]:
    """Preview expected optimization outcomes."""
    prefs = _pref_manager.get_or_create(user_id)
    result = _opt_configurator.preview(prefs.optimization)
    return result.model_dump()


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

@router.get("/presets")
async def list_presets() -> list[dict[str, Any]]:
    """List all available presets (built-in + custom)."""
    presets = _preset_manager.list_presets()
    return [p.model_dump() for p in presets]


@router.post("/presets")
async def create_preset(
    name: str,
    user_id: str = "default",
    description: str = "",
    domain: str = "general",
) -> dict[str, Any]:
    """Create a new preset from current user preferences."""
    prefs = _pref_manager.get(user_id)
    if prefs is None:
        raise HTTPException(status_code=404, detail="No preferences found for this user.")
    try:
        domain_enum = DomainType(domain)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid domain: {domain}. Valid: {[d.value for d in DomainType]}",
        )
    preset = await _preset_manager.create_preset(
        name,
        prefs,
        description=description,
        domain=domain_enum,
    )
    return preset.model_dump()


@router.post("/presets/{preset_id}/apply")
async def apply_preset(preset_id: str, user_id: str = "default") -> dict[str, Any]:
    """Apply a preset to the user's preferences."""
    new_prefs = _preset_manager.apply_preset(preset_id)
    if new_prefs is None:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found.")
    new_prefs.user_id = user_id
    saved = _pref_manager.save(new_prefs)
    return {
        "preset_id": preset_id,
        "preferences": saved.model_dump(mode="json"),
    }


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: str) -> dict[str, str]:
    """Delete a custom preset."""
    try:
        removed = _preset_manager.delete_preset(preset_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found.")
    return {"status": "deleted", "preset_id": preset_id}


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

@router.get("/smart-defaults")
async def get_smart_defaults(
    user_id: str = "default",
    domain: str = "general",
    avg_cost: float = 0.5,
    avg_latency_ms: float = 2000.0,
    avg_quality: float = 0.75,
    total_runs: int = 0,
) -> dict[str, Any]:
    """Get AI-powered configuration recommendations."""
    try:
        domain_enum = DomainType(domain)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid domain: {domain}. Valid: {[d.value for d in DomainType]}",
        )
    stats = PipelineUsageStats(
        avg_cost_usd=avg_cost,
        avg_latency_ms=avg_latency_ms,
        avg_quality=avg_quality,
        total_runs=total_runs,
        domain=domain_enum,
    )
    engine = SmartDefaults(stats)
    result = engine.generate()
    return result.model_dump()


# ---------------------------------------------------------------------------
# Config schema (for UI generation)
# ---------------------------------------------------------------------------

@router.get("/schema")
async def get_config_schema() -> dict[str, Any]:
    """Get the full JSON Schema for the tuning interface."""
    return _schema_gen.to_json_schema()
