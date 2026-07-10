"""Repository pattern for EvalOps database access.

Provides a generic :class:`BaseRepository` with CRUD helpers plus
domain-specific repositories for Pipeline, Trace, Eval, Sweep, and Plugin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import DeclarativeBase

from backend.db.models import (
    EvalResult,
    Pipeline,
    PipelineRun,
    Plugin,
    Sweep,
    SweepTrial,
    Trace,
    TraceStep,
    UserConfig,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT", bound=DeclarativeBase)


# ---------------------------------------------------------------------------
# Base repository
# ---------------------------------------------------------------------------


class BaseRepository[ModelT]:
    """Generic CRUD operations for any SQLAlchemy model."""

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, record_id: str) -> ModelT | None:
        return await self._session.get(self._model, record_id)

    async def create(self, **kwargs: Any) -> ModelT:
        instance = self._model(**kwargs)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(self, record_id: str, **kwargs: Any) -> ModelT | None:
        instance = await self.get(record_id)
        if instance is None:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        await self._session.flush()
        return instance

    async def delete(self, record_id: str) -> bool:
        instance = await self.get(record_id)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> Sequence[ModelT]:
        stmt = select(self._model).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

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

    async def get_by_name(self, name: str) -> Pipeline | None:
        stmt = select(Pipeline).where(Pipeline.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_status(
        self, status: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Pipeline]:
        stmt = (
            select(Pipeline)
            .where(Pipeline.status == status)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_tag(
        self, tag: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Pipeline]:
        stmt = (
            select(Pipeline)
            .where(Pipeline.tags.op("jsonb_contains")(f'"{tag}"'))
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TraceRepository(BaseRepository[Trace]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Trace)

    async def list_by_pipeline(
        self, pipeline_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Trace]:
        stmt = (
            select(Trace)
            .where(Trace.pipeline_id == pipeline_id)
            .order_by(Trace.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_run(
        self, run_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Trace]:
        stmt = (
            select(Trace)
            .where(Trace.run_id == run_id)
            .order_by(Trace.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_status(
        self, status: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Trace]:
        stmt = (
            select(Trace)
            .where(Trace.status == status)
            .order_by(Trace.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_with_steps(self, trace_id: str) -> Trace | None:
        stmt = select(Trace).where(Trace.id == trace_id)
        result = await self._session.execute(stmt)
        trace = result.scalar_one_or_none()
        if trace is not None:
            stmt_steps = (
                select(TraceStep)
                .where(TraceStep.trace_id == trace_id)
                .order_by(TraceStep.id)
            )
            steps_result = await self._session.execute(stmt_steps)
            trace.trace_steps = list(steps_result.scalars().all())
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
    ) -> Sequence[PipelineRun]:
        stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_id == pipeline_id)
            .order_by(PipelineRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_latest(self, pipeline_id: str) -> PipelineRun | None:
        stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_id == pipeline_id)
            .order_by(PipelineRun.started_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


class EvalRepository(BaseRepository[EvalResult]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EvalResult)

    async def list_by_trajectory(
        self, trajectory_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[EvalResult]:
        stmt = (
            select(EvalResult)
            .where(EvalResult.trajectory_id == trajectory_id)
            .order_by(EvalResult.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_best_for_trajectory(
        self, trajectory_id: str
    ) -> EvalResult | None:
        stmt = (
            select(EvalResult)
            .where(EvalResult.trajectory_id == trajectory_id)
            .order_by(EvalResult.aggregate_score.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_high_scores(
        self, min_score: float, *, offset: int = 0, limit: int = 50
    ) -> Sequence[EvalResult]:
        stmt = (
            select(EvalResult)
            .where(EvalResult.aggregate_score >= min_score)
            .order_by(EvalResult.aggregate_score.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


class SweepRepository(BaseRepository[Sweep]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Sweep)

    async def list_by_pipeline(
        self, pipeline_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Sweep]:
        stmt = (
            select(Sweep)
            .where(Sweep.pipeline_id == pipeline_id)
            .order_by(Sweep.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_active(self, pipeline_id: str) -> Sweep | None:
        stmt = (
            select(Sweep)
            .where(Sweep.pipeline_id == pipeline_id, Sweep.status == "running")
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_trial(self, sweep_id: str, **kwargs: Any) -> SweepTrial:
        trial = SweepTrial(sweep_id=sweep_id, **kwargs)
        self._session.add(trial)
        await self._session.flush()
        return trial

    async def list_trials(
        self, sweep_id: str, *, offset: int = 0, limit: int = 500
    ) -> Sequence[SweepTrial]:
        stmt = (
            select(SweepTrial)
            .where(SweepTrial.sweep_id == sweep_id)
            .order_by(SweepTrial.trial_number)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class PluginRepository(BaseRepository[Plugin]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Plugin)

    async def get_by_name(self, name: str) -> Plugin | None:
        stmt = select(Plugin).where(Plugin.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_type(
        self, plugin_type: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[Plugin]:
        stmt = (
            select(Plugin)
            .where(Plugin.plugin_type == plugin_type)
            .order_by(Plugin.downloads.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def increment_downloads(self, plugin_id: str) -> None:
        plugin = await self.get(plugin_id)
        if plugin is not None:
            plugin.downloads += 1
            await self._session.flush()

    async def search(self, query: str, *, limit: int = 20) -> Sequence[Plugin]:
        stmt = (
            select(Plugin)
            .where(
                Plugin.name.ilike(f"%{query}%")
                | Plugin.description.ilike(f"%{query}%")
            )
            .order_by(Plugin.downloads.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# UserConfig
# ---------------------------------------------------------------------------


class UserConfigRepository(BaseRepository[UserConfig]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserConfig)

    async def get_for_user_pipeline(
        self, user_id: str, pipeline_id: str
    ) -> UserConfig | None:
        stmt = select(UserConfig).where(
            UserConfig.user_id == user_id,
            UserConfig.pipeline_id == pipeline_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: str, *, offset: int = 0, limit: int = 50
    ) -> Sequence[UserConfig]:
        stmt = (
            select(UserConfig)
            .where(UserConfig.user_id == user_id)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
