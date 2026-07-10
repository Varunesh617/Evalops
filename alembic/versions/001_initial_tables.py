"""Initial database tables.

Revision ID: 001_initial
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- pipelines ---
    op.create_table(
        "pipelines",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), server_default="draft"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pipelines_status", "pipelines", ["status"])
    op.create_index("ix_pipelines_created_at", "pipelines", ["created_at"])

    # --- pipeline_runs ---
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.String(32),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("config_overrides", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("trace_sample_rate", sa.Float(), server_default="1.0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pipeline_runs_pipeline_id", "pipeline_runs", ["pipeline_id"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])

    # --- traces ---
    op.create_table(
        "traces",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.String(32),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("query", sa.Text(), server_default=""),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("total_tokens", sa.Integer(), server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), server_default="0.0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_traces_pipeline_id", "traces", ["pipeline_id"])
    op.create_index("ix_traces_run_id", "traces", ["run_id"])
    op.create_index("ix_traces_status", "traces", ["status"])
    op.create_index("ix_traces_started_at", "traces", ["started_at"])

    # --- trace_steps ---
    op.create_table(
        "trace_steps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "trace_id",
            sa.String(32),
            sa.ForeignKey("traces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("start_time", sa.Float(), nullable=True),
        sa.Column("end_time", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("tokens", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(256), nullable=True),
    )
    op.create_index("ix_trace_steps_trace_id", "trace_steps", ["trace_id"])
    op.create_index("ix_trace_steps_step_name", "trace_steps", ["step_name"])

    # --- eval_results ---
    op.create_table(
        "eval_results",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("trajectory_id", sa.String(256), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("aggregate_score", sa.Float(), server_default="0.0"),
        sa.Column("metric_details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_eval_results_trajectory_id", "eval_results", ["trajectory_id"])
    op.create_index("ix_eval_results_created_at", "eval_results", ["created_at"])

    # --- sweeps ---
    op.create_table(
        "sweeps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.String(32),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("search_space", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("objective", sa.String(128), server_default="aggregate_score"),
        sa.Column("n_trials", sa.Integer(), server_default="50"),
        sa.Column("trials_completed", sa.Integer(), server_default="0"),
        sa.Column("best_value", sa.Float(), nullable=True),
        sa.Column("best_params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sweeps_pipeline_id", "sweeps", ["pipeline_id"])
    op.create_index("ix_sweeps_status", "sweeps", ["status"])

    # --- sweep_trials ---
    op.create_table(
        "sweep_trials",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "sweep_id",
            sa.String(32),
            sa.ForeignKey("sweeps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trial_number", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
    )
    op.create_index("ix_sweep_trials_sweep_id", "sweep_trials", ["sweep_id"])
    op.create_index("ix_sweep_trials_trial_number", "sweep_trials", ["trial_number"])

    # --- plugins ---
    op.create_table(
        "plugins",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("author", sa.String(256), server_default=""),
        sa.Column("plugin_type", sa.String(32), nullable=False),
        sa.Column("entry_point", sa.String(512), nullable=False),
        sa.Column("config_schema", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("downloads", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_plugins_plugin_type", "plugins", ["plugin_type"])

    # --- user_configs ---
    op.create_table(
        "user_configs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(256), nullable=False),
        sa.Column(
            "pipeline_id",
            sa.String(32),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selected_metrics", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("selected_filters", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("custom_thresholds", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_user_configs_user_id", "user_configs", ["user_id"])
    op.create_index("ix_user_configs_pipeline_id", "user_configs", ["pipeline_id"])


def downgrade() -> None:
    op.drop_table("user_configs")
    op.drop_table("plugins")
    op.drop_table("sweep_trials")
    op.drop_table("sweeps")
    op.drop_table("eval_results")
    op.drop_table("trace_steps")
    op.drop_table("traces")
    op.drop_table("pipeline_runs")
    op.drop_table("pipelines")
