"""Repository pattern for EvalOps — SQLAlchemy ORM-backed implementations.

These repositories match the exact interface of the in-memory repositories
(returning dicts, using *page* / *page_size* parameters, etc.) so they are
drop-in replacements when ``DATABASE_URL`` is set.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import DeclarativeBase

from backend.db.models import (
    AppliedRecommendation,
    EvalResult,
    Pipeline,
    PipelineRun,
    Plugin,
    PluginState,
    Sweep,
    SweepTrial,
    Trace,
    TraceStep,
    TuningPreset,
    UserConfig,
    UserPreferenceState,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT", bound=DeclarativeBase)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obj_to_dict(obj: DeclarativeBase) -> dict[str, Any]:
    """Convert an ORM instance to a plain dict, stripping SA internals."""
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def _escape_like(value: str) -> str:
    """Escape ``%``, ``_`` and ``\\`` for use in SQL LIKE patterns."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Base repository
# ---------------------------------------------------------------------------


class BaseRepository[ModelT]:
    """Generic CRUD operations for any SQLAlchemy model.

    The interface mirrors the in-memory repositories: *create* takes a dict,
    *get* / *update* return dicts, and *list* returns a ``(items, total)``
    tuple with *page* / *page_size* pagination.
    """

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, record_id: str) -> dict[str, Any] | None:
        instance = await self._session.get(self._model, record_id)
        return _obj_to_dict(instance) if instance is not None else None

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        instance = self._model(**record)
        self._session.add(instance)
        await self._session.flush()
        return _obj_to_dict(instance)

    async def update(
        self, record_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        instance = await self._session.get(self._model, record_id)
        if instance is None:
            return None
        for key, value in updates.items():
            setattr(instance, key, value)
        await self._session.flush()
        return _obj_to_dict(instance)

    async def delete(self, record_id: str) -> bool:
        instance = await self._session.get(self._model, record_id)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        total = await self.count()
        stmt = select(self._model).offset(offset).limit(page_size)
        result = await self._session.execute(stmt)
        items = [_obj_to_dict(o) for o in result.scalars().all()]
        return items, total

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self._model)
        result = await self._session.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class PipelineRepository(BaseRepository[Pipeline]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Pipeline)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        stmt = select(Pipeline).where(Pipeline.name == name)
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None

    async def list(
        self,
        *,
        status: str | None = None,
        tag: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size

        conditions = []
        if status is not None:
            conditions.append(Pipeline.status == status)
        if tag is not None:
            # M1: Portable JSON array contains — works on both PostgreSQL and
            # SQLite by casting the JSON column to text and using LIKE.
            tag_literal = json.dumps(tag)  # e.g. '"production"'
            conditions.append(
                cast(Pipeline.tags, String).like(
                    f"%{_escape_like(tag_literal)}%", escape="\\"
                )
            )

        base = select(Pipeline)
        if conditions:
            base = base.where(*conditions)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = base.order_by(Pipeline.created_at.desc()).offset(offset).limit(page_size)
        result = await self._session.execute(stmt)
        items = [_obj_to_dict(o) for o in result.scalars().all()]
        return items, total

    async def update(
        self, pipeline_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        return await super().update(pipeline_id, updates)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TraceRepository(BaseRepository[Trace]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Trace)

    async def list(
        self,
        *,
        pipeline_id: str | None = None,
        status: str | None = None,
        min_cost: float | None = None,
        max_cost: float | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size

        conditions: list = []
        if pipeline_id is not None:
            conditions.append(Trace.pipeline_id == pipeline_id)
        if status is not None:
            conditions.append(Trace.status == status)
        if min_cost is not None:
            conditions.append(Trace.total_cost_usd >= min_cost)
        if max_cost is not None:
            conditions.append(Trace.total_cost_usd <= max_cost)

        base = select(Trace)
        if conditions:
            base = base.where(*conditions)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = base.order_by(Trace.started_at.desc()).offset(offset).limit(page_size)
        result = await self._session.execute(stmt)
        items = [_obj_to_dict(o) for o in result.scalars().all()]
        return items, total

    async def get_with_steps(self, trace_id: str) -> dict[str, Any] | None:
        trace = await self.get(trace_id)
        if trace is None:
            return None
        stmt_steps = (
            select(TraceStep)
            .where(TraceStep.trace_id == trace_id)
            .order_by(TraceStep.id)
        )
        steps_result = await self._session.execute(stmt_steps)
        trace["trace_steps"] = [_obj_to_dict(s) for s in steps_result.scalars().all()]
        return trace

    async def count_by_pipeline(self, pipeline_id: str) -> int:
        stmt = select(func.count()).select_from(Trace).where(
            Trace.pipeline_id == pipeline_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# PipelineRun
# ---------------------------------------------------------------------------


class RunRepository(BaseRepository[PipelineRun]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineRun)

    async def list_by_pipeline(
        self, pipeline_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_id == pipeline_id)
            .order_by(PipelineRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]

    async def get_latest(self, pipeline_id: str) -> dict[str, Any] | None:
        stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_id == pipeline_id)
            .order_by(PipelineRun.started_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


class EvalRepository(BaseRepository[EvalResult]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EvalResult)

    async def list_by_trajectory(
        self, trajectory_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(EvalResult)
            .where(EvalResult.trajectory_id == trajectory_id)
            .order_by(EvalResult.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]

    async def get_best_for_trajectory(
        self, trajectory_id: str
    ) -> dict[str, Any] | None:
        stmt = (
            select(EvalResult)
            .where(EvalResult.trajectory_id == trajectory_id)
            .order_by(EvalResult.aggregate_score.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None

    async def list_high_scores(
        self, min_score: float, *, offset: int = 0, limit: int = 50
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(EvalResult)
            .where(EvalResult.aggregate_score >= min_score)
            .order_by(EvalResult.aggregate_score.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


class SweepRepository(BaseRepository[Sweep]):
    """SQLAlchemy-backed sweep repository.

    The routes pass dicts with a ``sweep_id`` key rather than ``id``.
    We transparently map ``sweep_id`` → ``id`` on create and add ``sweep_id``
    back on reads so the rest of the application stays unchanged.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Sweep)

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        mapped = dict(record)
        if "sweep_id" in mapped:
            mapped["id"] = mapped.pop("sweep_id")
        result = await super().create(mapped)
        result["sweep_id"] = result["id"]
        return result

    async def get(self, sweep_id: str) -> dict[str, Any] | None:
        result = await super().get(sweep_id)
        if result is not None:
            result["sweep_id"] = result["id"]
        return result

    async def update(
        self, sweep_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        result = await super().update(sweep_id, updates)
        if result is not None:
            result["sweep_id"] = result["id"]
        return result

    async def get_active(self, pipeline_id: str) -> dict[str, Any] | None:
        stmt = (
            select(Sweep)
            .where(Sweep.pipeline_id == pipeline_id, Sweep.status == "running")
            .limit(1)
        )
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        if obj is None:
            return None
        d = _obj_to_dict(obj)
        d["sweep_id"] = d["id"]
        return d

    async def add_trial(self, sweep_id: str, **kwargs: Any) -> dict[str, Any]:
        trial = SweepTrial(sweep_id=sweep_id, **kwargs)
        self._session.add(trial)
        await self._session.flush()
        return _obj_to_dict(trial)

    async def list_trials(
        self, sweep_id: str, *, offset: int = 0, limit: int = 500
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(SweepTrial)
            .where(SweepTrial.sweep_id == sweep_id)
            .order_by(SweepTrial.trial_number)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class PluginRepository(BaseRepository[Plugin]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Plugin)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        stmt = select(Plugin).where(Plugin.name == name)
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None

    async def list_by_type(
        self, plugin_type: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(Plugin)
            .where(Plugin.plugin_type == plugin_type)
            .order_by(Plugin.downloads.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]

    async def increment_downloads(self, plugin_id: str) -> None:
        plugin = await self._session.get(Plugin, plugin_id)
        if plugin is not None:
            plugin.downloads += 1
            await self._session.flush()

    async def search(self, query: str, *, limit: int = 20) -> Sequence[dict[str, Any]]:
        # M2: LIKE wildcard injection — escape user input before interpolating
        safe = _escape_like(query)
        stmt = (
            select(Plugin)
            .where(
                Plugin.name.ilike(f"%{safe}%", escape="\\")
                | Plugin.description.ilike(f"%{safe}%", escape="\\")
            )
            .order_by(Plugin.downloads.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]


# ---------------------------------------------------------------------------
# UserConfig
# ---------------------------------------------------------------------------


class UserConfigRepository(BaseRepository[UserConfig]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserConfig)

    async def get_for_user_pipeline(
        self, user_id: str, pipeline_id: str
    ) -> dict[str, Any] | None:
        stmt = select(UserConfig).where(
            UserConfig.user_id == user_id,
            UserConfig.pipeline_id == pipeline_id,
        )
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None

    async def list_for_user(
        self, user_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(UserConfig)
            .where(UserConfig.user_id == user_id)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]


# ---------------------------------------------------------------------------
# PluginState
# ---------------------------------------------------------------------------


class PluginStateRepository(BaseRepository[PluginState]):
    """Persisted mirror of the in-memory PluginRegistry."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PluginState)

    async def upsert(self, plugin_id: str, **fields: Any) -> dict[str, Any] | None:
        """Insert or update a plugin state row, returning the resulting dict."""
        existing = await self._session.get(PluginState, plugin_id)
        if existing is None:
            record = {"plugin_id": plugin_id, **fields}
            return await self.create(record)
        return await self.update(plugin_id, fields)

    async def get_enabled(self) -> Sequence[dict[str, Any]]:
        """Return all rows that are currently enabled."""
        stmt = select(PluginState).where(PluginState.enabled.is_(True))
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]


# ---------------------------------------------------------------------------
# TuningPreset
# ---------------------------------------------------------------------------


class TuningPresetRepository(BaseRepository[TuningPreset]):
    """Persisted custom tuning presets."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, TuningPreset)

    async def list_for_user(
        self, user_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[dict[str, Any]]:
        stmt = (
            select(TuningPreset)
            .where(TuningPreset.user_id == user_id)
            .order_by(TuningPreset.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()]

    async def delete_custom(self, preset_id: str) -> bool:
        """Delete a preset, refusing to remove built-in rows.

        Returns ``False`` if the row does not exist or is a built-in preset.
        """
        instance = await self._session.get(TuningPreset, preset_id)
        if instance is None or instance.is_builtin:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True


# ---------------------------------------------------------------------------
# UserPreferenceState
# ---------------------------------------------------------------------------


class UserPreferenceRepository(BaseRepository[UserPreferenceState]):
    """Persisted user tuning preferences keyed by user_id."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserPreferenceState)

    async def get_for_user(self, user_id: str) -> dict[str, Any] | None:
        """Return the preference state row for a user, or ``None``."""
        stmt = select(UserPreferenceState).where(UserPreferenceState.user_id == user_id)
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None

    async def upsert(self, user_id: str, *, preferences_json: dict[str, Any]) -> dict[str, Any]:
        """Insert or update the preference state for a user."""
        existing = await self._session.get(UserPreferenceState, user_id)
        if existing is None:
            record = {
                "id": user_id,
                "user_id": user_id,
                "preferences_json": preferences_json,
            }
            return await self.create(record)
        return await self.update(user_id, {"preferences_json": preferences_json})


# ---------------------------------------------------------------------------
# AppliedRecommendation
# ---------------------------------------------------------------------------


class AppliedRecommendationRepository(BaseRepository[AppliedRecommendation]):
    """Tracks applied recommendations and their measured outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AppliedRecommendation)

    async def list_for_user(
        self, user_id: str, *, page: int = 1, page_size: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        total_stmt = select(func.count()).select_from(AppliedRecommendation).where(
            AppliedRecommendation.user_id == user_id
        )
        total = (await self._session.execute(total_stmt)).scalar_one()
        stmt = (
            select(AppliedRecommendation)
            .where(AppliedRecommendation.user_id == user_id)
            .order_by(AppliedRecommendation.applied_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        return [_obj_to_dict(o) for o in result.scalars().all()], total

    async def get_for_recommendation(
        self, recommendation_id: str
    ) -> dict[str, Any] | None:
        """Return the applied record for a unique recommendation id, if any."""
        stmt = select(AppliedRecommendation).where(
            AppliedRecommendation.recommendation_id == recommendation_id
        )
        result = await self._session.execute(stmt)
        obj = result.scalar_one_or_none()
        return _obj_to_dict(obj) if obj is not None else None

    async def update_outcome(
        self,
        recommendation_id: str,
        *,
        outcome_status: str,
        measured_delta: float | None = None,
        measured_cost_delta: float | None = None,
        measured_latency_delta_ms: float | None = None,
        outcome_notes: str = "",
    ) -> dict[str, Any] | None:
        """Record the outcome of an already-applied recommendation.

        The *recommendation_id* is the application's unique id (also stored as
        the synthetic primary key), so we look the row up by that column and
        then update it by primary key.
        """
        stmt = select(AppliedRecommendation).where(
            AppliedRecommendation.recommendation_id == recommendation_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await self.update(
            row.id,
            {
                "outcome_status": outcome_status,
                "measured_delta": measured_delta,
                "measured_cost_delta": measured_cost_delta,
                "measured_latency_delta_ms": measured_latency_delta_ms,
                "outcome_notes": outcome_notes,
            },
        )


# ---------------------------------------------------------------------------
# AppliedRecommendation (single canonical definition)
# ---------------------------------------------------------------------------
