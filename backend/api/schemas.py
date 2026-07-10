"""Request/response schemas for the EvalOps API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

class PipelineStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineCreate(BaseModel):
    """Request body to create a pipeline configuration."""

    name: str = Field(..., min_length=1, max_length=256)
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class PipelineResponse(BaseModel):
    """Response for a pipeline entity."""

    id: str
    name: str
    description: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: PipelineStatus = PipelineStatus.DRAFT
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PipelineListResponse(BaseModel):
    pipelines: list[PipelineResponse]
    total: int
    page: int
    page_size: int


class PipelineRunRequest(BaseModel):
    """Request body to trigger a pipeline run."""

    config_overrides: dict[str, Any] = Field(default_factory=dict)
    trace_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class PipelineRunResponse(BaseModel):
    run_id: str
    pipeline_id: str
    status: str
    started_at: datetime


# ---------------------------------------------------------------------------
# Eval schemas
# ---------------------------------------------------------------------------

class EvalRunRequest(BaseModel):
    """Request to run an evaluation on a trajectory."""

    trajectory: dict[str, Any] = Field(..., description="Trajectory to evaluate")
    metrics: list[str] = Field(
        default_factory=lambda: ["faithfulness", "context_relevance"],
        description="Metrics to compute",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalResultResponse(BaseModel):
    id: str
    trajectory_id: str
    scores: dict[str, float]
    aggregate_score: float
    metric_details: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    created_at: datetime


class EvalCompareRequest(BaseModel):
    eval_ids: list[str] = Field(..., min_length=2, max_length=10)


class EvalCompareResponse(BaseModel):
    eval_a: EvalResultResponse
    eval_b: EvalResultResponse
    score_diffs: dict[str, float]
    winner: str | None


# ---------------------------------------------------------------------------
# Trace schemas
# ---------------------------------------------------------------------------

class TraceStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TraceStepResponse(BaseModel):
    step_id: int
    step_type: str
    input_text: str = ""
    output_text: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    status: str = "completed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceResponse(BaseModel):
    id: str
    pipeline_id: str
    query: str
    status: TraceStatus
    steps: list[TraceStepResponse] = Field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    started_at: datetime
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceListResponse(BaseModel):
    traces: list[TraceResponse]
    total: int
    page: int
    page_size: int


class BlameAttribution(BaseModel):
    trace_id: str
    failure_step: int
    failure_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    root_cause: str
    contributing_factors: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Optimization schemas
# ---------------------------------------------------------------------------

class SweepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SweepRequest(BaseModel):
    """Request to start a hyperparameter sweep."""

    pipeline_id: str
    search_space: dict[str, Any] = Field(
        ...,
        description="Optuna-style search space definition",
    )
    objective: str = Field(
        default="aggregate_score",
        description="Metric to optimize",
    )
    n_trials: int = Field(default=50, ge=1, le=500)
    timeout_seconds: float = Field(default=3600.0, ge=60.0, le=86400.0)


class SweepStatusResponse(BaseModel):
    sweep_id: str
    pipeline_id: str
    status: SweepStatus
    trials_completed: int
    best_value: float | None = None
    best_params: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    estimated_completion: datetime | None = None


class ParetoPoint(BaseModel):
    params: dict[str, Any]
    objectives: dict[str, float]
    rank: int = 0


class ParetoResponse(BaseModel):
    sweep_id: str
    frontier: list[ParetoPoint]
    total_points: int


# ---------------------------------------------------------------------------
# Deployment schemas
# ---------------------------------------------------------------------------


class DeployRequest(BaseModel):
    """Request to deploy an optimized pipeline config."""

    pipeline_id: str
    config: dict[str, Any] = Field(..., description="PipelineConfig to deploy")
    quality_score: float = Field(ge=0.0, le=1.0)
    cost_usd: float = Field(ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    trials_completed: int = Field(default=0, ge=0)
    tests_passed: bool = True
    message: str = ""


class DeployResponse(BaseModel):
    deployment_id: str
    pipeline_id: str
    status: str
    config_diff: dict[str, Any] = Field(default_factory=dict)
    previous_config: dict[str, Any] = Field(default_factory=dict)
    new_config: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    rollback_id: str | None = None
    created_at: datetime


class RollbackRequest(BaseModel):
    """Request to rollback a pipeline to its previous config."""

    pipeline_id: str


class DeploymentRecordResponse(BaseModel):
    deployment_id: str
    pipeline_id: str
    status: str
    message: str = ""
    created_at: datetime


class DeploymentHistoryResponse(BaseModel):
    pipeline_id: str
    deployments: list[DeploymentRecordResponse]
    total: int


# ---------------------------------------------------------------------------
# Cost schemas
# ---------------------------------------------------------------------------


class CostBucketResponse(BaseModel):
    label: str
    total_cost_usd: float
    entry_count: int
    avg_cost_usd: float
    avg_latency_ms: float
    total_tokens: int


class CostForecastResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    projected_cost_usd: float
    confidence: float


class CostAnomalyResponse(BaseModel):
    entry_id: str
    pipeline_id: str
    model: str
    cost_usd: float
    expected_cost_usd: float
    deviation_ratio: float


class CostReportResponse(BaseModel):
    total_cost_usd: float
    total_entries: int
    period_start: datetime | None = None
    period_end: datetime | None = None
    by_pipeline: list[CostBucketResponse] = Field(default_factory=list)
    by_model: list[CostBucketResponse] = Field(default_factory=list)
    by_user: list[CostBucketResponse] = Field(default_factory=list)
    daily_costs: list[dict[str, Any]] = Field(default_factory=list)
    forecasts: list[CostForecastResponse] = Field(default_factory=list)
    anomalies: list[CostAnomalyResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Experiment schemas
# ---------------------------------------------------------------------------


class ExperimentLogResponse(BaseModel):
    experiment_id: str
    pipeline_id: str
    run_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    source: str = "local"
    created_at: datetime


class ExperimentHistoryResponse(BaseModel):
    pipeline_id: str
    experiments: list[ExperimentLogResponse]
    total: int


class ExperimentSearchRequest(BaseModel):
    """Request to search experiments."""

    pipeline_id: str | None = None
    min_metrics: dict[str, float] | None = None
    tags: dict[str, str] | None = None
    after: datetime | None = None
    before: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)


class ExperimentCompareResponse(BaseModel):
    experiment_a: ExperimentLogResponse
    experiment_b: ExperimentLogResponse
    metric_diffs: dict[str, float] = Field(default_factory=dict)
    param_diffs: dict[str, Any] = Field(default_factory=dict)
    better_experiment: str | None = None
    primary_metric: str = "composite_score"


# ---------------------------------------------------------------------------
# WebSocket schemas
# ---------------------------------------------------------------------------

class WSSubscription(BaseModel):
    """WebSocket subscription request."""

    pipeline_id: str | None = None
    trace_id: str | None = None
    event_types: list[str] = Field(
        default_factory=lambda: ["trace_start", "trace_step", "trace_end"],
    )


class WSTraceEvent(BaseModel):
    event_type: str
    pipeline_id: str
    trace_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
