"""SQLAlchemy ORM models for EvalOps.

Tables:
    Pipeline, PipelineRun, Trace, TraceStep, EvalResult,
    Sweep, SweepTrial, Plugin, UserConfig
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow, onupdate=_utcnow
    )

    runs: Mapped[list[PipelineRun]] = relationship(back_populates="pipeline")
    traces: Mapped[list[Trace]] = relationship(back_populates="pipeline")
    sweeps: Mapped[list[Sweep]] = relationship(back_populates="pipeline")
    user_configs: Mapped[list[UserConfig]] = relationship(back_populates="pipeline")

    __table_args__ = (
        Index("ix_pipelines_status", "status"),
        Index("ix_pipelines_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# PipelineRun
# ---------------------------------------------------------------------------


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    pipeline_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    config_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_sample_rate: Mapped[float] = mapped_column(Float, default=1.0)
    started_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)

    pipeline: Mapped[Pipeline] = relationship(back_populates="runs")
    traces: Mapped[list[Trace]] = relationship(back_populates="run")

    __table_args__ = (
        Index("ix_pipeline_runs_pipeline_id", "pipeline_id"),
        Index("ix_pipeline_runs_status", "status"),
    )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    pipeline_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True
    )
    query: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    trace_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict
    )

    pipeline: Mapped[Pipeline] = relationship(back_populates="traces")
    run: Mapped[PipelineRun | None] = relationship(back_populates="traces")
    trace_steps: Mapped[list[TraceStep]] = relationship(
        back_populates="trace", order_by="TraceStep.id"
    )

    __table_args__ = (
        Index("ix_traces_pipeline_id", "pipeline_id"),
        Index("ix_traces_run_id", "run_id"),
        Index("ix_traces_status", "status"),
        Index("ix_traces_started_at", "started_at"),
    )


# ---------------------------------------------------------------------------
# TraceStep
# ---------------------------------------------------------------------------


class TraceStep(Base):
    __tablename__ = "trace_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    trace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    start_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(256), nullable=True)

    trace: Mapped[Trace] = relationship(back_populates="trace_steps")

    __table_args__ = (
        Index("ix_trace_steps_trace_id", "trace_id"),
        Index("ix_trace_steps_step_name", "step_name"),
    )


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    trajectory_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scores: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    aggregate_score: Mapped[float] = mapped_column(Float, default=0.0)
    metric_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )

    __table_args__ = (
        Index("ix_eval_results_trajectory_id", "trajectory_id"),
        Index("ix_eval_results_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


class Sweep(Base):
    __tablename__ = "sweeps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    pipeline_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    search_space: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    objective: Mapped[str] = mapped_column(String(128), default="aggregate_score")
    n_trials: Mapped[int] = mapped_column(Integer, default=50)
    trials_completed: Mapped[int] = mapped_column(Integer, default=0)
    best_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)

    pipeline: Mapped[Pipeline] = relationship(back_populates="sweeps")
    trials: Mapped[list[SweepTrial]] = relationship(
        back_populates="sweep", order_by="SweepTrial.trial_number"
    )

    __table_args__ = (
        Index("ix_sweeps_pipeline_id", "pipeline_id"),
        Index("ix_sweeps_status", "status"),
    )


# ---------------------------------------------------------------------------
# SweepTrial
# ---------------------------------------------------------------------------


class SweepTrial(Base):
    __tablename__ = "sweep_trials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    sweep_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sweeps.id", ondelete="CASCADE"), nullable=False
    )
    trial_number: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    sweep: Mapped[Sweep] = relationship(back_populates="trials")

    __table_args__ = (
        Index("ix_sweep_trials_sweep_id", "sweep_id"),
        Index("ix_sweep_trials_trial_number", "trial_number"),
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class Plugin(Base):
    __tablename__ = "plugins"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(256), default="")
    plugin_type: Mapped[str] = mapped_column(String(32), nullable=False)  # metric | filter
    entry_point: Mapped[str] = mapped_column(String(512), nullable=False)
    config_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_plugins_plugin_type", "plugin_type"),
    )


# ---------------------------------------------------------------------------
# UserConfig
# ---------------------------------------------------------------------------


class UserConfig(Base):
    __tablename__ = "user_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    pipeline_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    selected_metrics: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_filters: Mapped[list[str]] = mapped_column(JSON, default=list)
    custom_thresholds: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=_utcnow, onupdate=_utcnow
    )

    pipeline: Mapped[Pipeline] = relationship(back_populates="user_configs")

    __table_args__ = (
        Index("ix_user_configs_user_id", "user_id"),
        Index("ix_user_configs_pipeline_id", "pipeline_id"),
    )
