"""Diagnosis routes — counterfactual analysis, recommendations, and historical trends."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.dependencies import get_blame_engine, get_trace_repository
from backend.core.config import StepStatus
from backend.core.tracer import (
    StepMetrics,
    TokenUsage,
    Trajectory,
    TrajectoryStep,
)
from backend.db.repositories import AppliedRecommendationRepository, TraceRepository
from backend.diagnosis.counterfactual import (
    ChangeType,
    CounterfactualEngine,
    Intervention,
    PipelineExecutor,
)
from backend.diagnosis.historical_analyzer import (
    FailureRecord,
    HistoricalAnalyzer,
)
from backend.diagnosis.recommender import RecommendationEngine
from backend.eval.blame_attribution import BlameAttributionEngine

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])

# ---------------------------------------------------------------------------
# Recommendation feedback store (3.2)
# ---------------------------------------------------------------------------
#
# When DATABASE_URL is set we persist applied recommendations via
# AppliedRecommendationRepository; otherwise we fall back to an in-memory
# dict so tests and local runs work without a database.

_applied_recommendation_repo: AppliedRecommendationRepository | None = None
_applied_store: dict[str, dict[str, Any]] = {}

if os.environ.get("DATABASE_URL"):
    try:
        from backend.db.session import get_session_factory

        _factory = get_session_factory()
        _applied_recommendation_repo = AppliedRecommendationRepository(_factory())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("diagnosis_db_init_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class CounterfactualRequest(BaseModel):
    """Request body for counterfactual analysis."""

    trace_id: str | None = Field(
        default=None,
        description="Trace ID to analyse (looked up from DB)",
    )
    trajectory: dict[str, Any] | None = Field(
        default=None,
        description="Inline trajectory payload (alternative to trace_id)",
    )


class CounterfactualResponse(BaseModel):
    report_id: str
    trace_id: str
    original_score: float
    results: list[dict[str, Any]]
    best_intervention: dict[str, Any] | None
    best_delta: float


class RecommendationResponse(BaseModel):
    trace_id: str
    recommendations: list[dict[str, Any]]
    total: int


class HistoricalQueryResponse(BaseModel):
    report_id: str
    total_failures: int
    time_range: dict[str, str | None]
    frequency: list[dict[str, Any]]
    recurring_patterns: list[dict[str, Any]]
    trend: str
    trend_confidence: float
    correlations: list[dict[str, Any]]
    failure_mode_distribution: dict[str, int]
    step_distribution: dict[str, int]
    avg_score: float
    page: int = 1
    page_size: int = 100
    total_traces: int = 0
    has_more: bool = False


class TrendDataPoint(BaseModel):
    period: str
    count: int
    failure_modes: dict[str, int]


class TrendsResponse(BaseModel):
    trend: str
    confidence: float
    data_points: list[TrendDataPoint]
    total_failures: int


# ---------------------------------------------------------------------------
# Real counterfactual re-run (3.1)
# ---------------------------------------------------------------------------


class RealRunRequest(BaseModel):
    """Request to actually re-run the pipeline for one intervention."""

    trace_id: str | None = Field(default=None, description="Trace ID to analyse")
    trajectory: dict[str, Any] | None = Field(
        default=None, description="Inline trajectory payload (alternative to trace_id)"
    )
    change_type: str = Field(
        description="Counterfactual ChangeType to apply (e.g. reasoning_model)"
    )
    original_value: Any | None = None
    counterfactual_value: Any | None = None
    description: str = ""
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)


class RealRunResponse(BaseModel):
    intervention: dict[str, Any]
    counterfactual_score: float
    improvement_delta: float
    confidence: float
    cost_usd: float
    latency_ms: float
    error: str | None
    original_step_scores: dict[str, float]
    counterfactual_step_scores: dict[str, float]


# ---------------------------------------------------------------------------
# Recommendation feedback loop (3.2)
# ---------------------------------------------------------------------------


class ApplyRecommendationRequest(BaseModel):
    """Record that a recommendation was applied for a trace."""

    trace_id: str
    recommendation_id: str
    category: str = "general"
    action: str = ""
    change_type: str = ""
    user_id: str = "default"


class ApplyRecommendationResponse(BaseModel):
    id: str
    user_id: str
    trace_id: str
    recommendation_id: str
    category: str
    action: str
    change_type: str
    applied_at: str
    outcome_status: str


class OutcomeUpdateRequest(BaseModel):
    """Update the measured outcome of an applied recommendation."""

    outcome_status: str
    measured_delta: float | None = None
    measured_cost_delta: float | None = None
    measured_latency_delta_ms: float | None = None
    outcome_notes: str = ""


class AppliedRecommendationView(BaseModel):
    id: str
    user_id: str
    trace_id: str
    recommendation_id: str
    category: str
    action: str
    change_type: str
    applied_at: str
    outcome_status: str
    measured_delta: float | None
    measured_cost_delta: float | None
    measured_latency_delta_ms: float | None
    outcome_notes: str
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconstruct_trajectory(record: dict[str, Any]) -> Trajectory:
    """Reconstruct a core Trajectory from a stored trace record."""
    raw_steps: list[dict[str, Any]] = record.get("steps", [])
    steps: list[TrajectoryStep] = []
    for s in raw_steps:
        tokens_raw = s.get("tokens", {})
        metrics_raw = s.get("metrics", {})
        steps.append(
            TrajectoryStep(
                step_id=s.get("step_id", ""),
                step_name=s.get("step_name", ""),
                status=StepStatus(s.get("status", "success")),
                latency_ms=s.get("latency_ms"),
                tokens=TokenUsage(
                    prompt_tokens=tokens_raw.get("prompt_tokens", 0),
                    completion_tokens=tokens_raw.get("completion_tokens", 0),
                    total_tokens=tokens_raw.get("total_tokens", 0),
                ),
                metrics=StepMetrics(
                    score=metrics_raw.get("score"),
                    confidence=metrics_raw.get("confidence"),
                    metadata=metrics_raw.get("metadata", {}),
                ),
                error=s.get("error"),
                error_type=s.get("error_type"),
                payload=s.get("payload", {}),
            )
        )

    total_tokens_raw = record.get("total_tokens", {})
    if isinstance(total_tokens_raw, int):
        total_tokens_raw = {"total_tokens": total_tokens_raw}

    return Trajectory(
        run_id=record.get("run_id", record["id"]),
        pipeline_id=record.get("pipeline_id", ""),
        steps=steps,
        total_tokens=TokenUsage(
            prompt_tokens=total_tokens_raw.get("prompt_tokens", 0),
            completion_tokens=total_tokens_raw.get("completion_tokens", 0),
            total_tokens=total_tokens_raw.get("total_tokens", 0),
        ),
        metadata=record.get("metadata", {}),
    )


async def _load_failure_records(
    trace_repo: TraceRepository,
    blame_engine: BlameAttributionEngine,
    *,
    page_size: int = 100,
) -> list[FailureRecord]:
    """Load all failed traces, reconstruct trajectories, and blame each one.

    Shared by ``/diagnosis/historical`` and ``/diagnosis/trends`` (3.4) so the
    paginated load + blame loop is not duplicated. Tolerates individual trace
    processing failures by logging and skipping.
    """
    records: list[FailureRecord] = []
    page = 1

    while True:
        items, total = await trace_repo.list(
            status="failed",
            page=page,
            page_size=page_size,
        )
        for item in items:
            try:
                trajectory = _reconstruct_trajectory(item)
                blame = blame_engine.analyse(trajectory)
                records.append(
                    FailureRecord(
                        trace_id=item.get("id", ""),
                        timestamp=item.get("started_at", datetime.now(UTC)),
                        root_cause_step=blame.root_cause_step,
                        failure_mode=str(blame.root_cause_mode),
                        severity=str(blame.severity),
                        score=blame.score,
                        pipeline_id=item.get("pipeline_id", ""),
                        model=item.get("metadata", {}).get("model", ""),
                    )
                )
            except Exception:
                logger.warning(
                    "failed_to_process_trace",
                    trace_id=item.get("id"),
                    exc_info=True,
                )

        if page * page_size >= total:
            break
        page += 1

    return records


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/counterfactual", response_model=CounterfactualResponse)
async def run_counterfactual(
    body: CounterfactualRequest,
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
) -> CounterfactualResponse:
    """Run counterfactual analysis on a failed trace.

    Provide either ``trace_id`` (to look up from DB) or an inline
    ``trajectory`` payload.
    """
    if body.trace_id is None and body.trajectory is None:
        raise HTTPException(
            status_code=422,
            detail="Provide either trace_id or trajectory",
        )

    # Obtain the trajectory
    trajectory: Trajectory
    if body.trace_id is not None:
        record = await trace_repo.get(body.trace_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Trace {body.trace_id} not found",
            )
        trajectory = _reconstruct_trajectory(record)
    else:
        # Build from inline payload — minimal reconstruction
        try:
            from backend.core.config import StepStatus as _Status

            raw_steps = body.trajectory.get("steps", [])
            steps = [
                TrajectoryStep(
                    step_name=s.get("step_name", ""),
                    status=_Status(s.get("status", "success")),
                    payload=s.get("payload", {}),
                    metrics=StepMetrics(
                        score=s.get("metrics", {}).get("score"),
                    ),
                )
                for s in raw_steps
            ]
            trajectory = Trajectory(
                run_id=body.trajectory.get("trajectory_id", "inline"),
                steps=steps,
                metadata=body.trajectory.get("metadata", {}),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid trajectory payload: {exc}",
            )

    # Check trajectory has failures
    if trajectory.succeeded:
        raise HTTPException(
            status_code=422,
            detail="Counterfactual analysis requires a failed trajectory",
        )

    # Run the engines
    blame = blame_engine.analyse(trajectory)
    cf_engine = CounterfactualEngine(blame_engine=blame_engine)
    cf_report = cf_engine.analyse(trajectory, blame=blame)

    logger.info(
        "counterfactual_analysis_completed",
        trace_id=body.trace_id or "inline",
        candidates=len(cf_report.results),
        best_delta=cf_report.best_delta,
    )

    return CounterfactualResponse(
        report_id=cf_report.report_id,
        trace_id=cf_report.trace_id,
        original_score=cf_report.original_score,
        results=[r.__dict__ for r in cf_report.results]
        if False  # avoid raw dataclass serialization
        else [
            {
                "intervention": {
                    "change_type": str(r.intervention.change_type),
                    "original_value": r.intervention.original_value,
                    "counterfactual_value": r.intervention.counterfactual_value,
                    "description": r.intervention.description,
                },
                "counterfactual_score": r.counterfactual_score,
                "improvement_delta": r.improvement_delta,
                "confidence": r.confidence,
                "original_step_scores": r.original_step_scores,
                "counterfactual_step_scores": r.counterfactual_step_scores,
            }
            for r in cf_report.results
        ],
        best_intervention=(
            {
                "change_type": str(cf_report.best_intervention.change_type),
                "description": cf_report.best_intervention.description,
            }
            if cf_report.best_intervention
            else None
        ),
        best_delta=cf_report.best_delta,
    )


@router.get("/recommendations/{trace_id}", response_model=RecommendationResponse)
async def get_recommendations(
    trace_id: str,
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
) -> RecommendationResponse:
    """Get actionable recommendations for a failed trace."""
    record = await trace_repo.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

    if record.get("status") != "failed":
        raise HTTPException(
            status_code=422,
            detail="Recommendations are only available for failed traces",
        )

    trajectory = _reconstruct_trajectory(record)
    blame = blame_engine.analyse(trajectory)

    # Optionally run counterfactuals for better recommendations
    cf_engine = CounterfactualEngine(blame_engine=blame_engine)
    cf_report = cf_engine.analyse(trajectory, blame=blame)

    rec_engine = RecommendationEngine()
    rec_report = rec_engine.recommend(
        blame,
        counterfactual_delta=cf_report.best_delta if cf_report.best_delta > 0 else None,
    )

    logger.info(
        "recommendations_generated",
        trace_id=trace_id,
        count=len(rec_report.recommendations),
    )

    return RecommendationResponse(
        trace_id=rec_report.trace_id,
        recommendations=[r.to_dict() for r in rec_report.prioritised],
        total=len(rec_report.recommendations),
    )


@router.get("/historical", response_model=HistoricalQueryResponse)
async def get_historical_analysis(
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
    time_window_days: int = Query(default=30, ge=1, le=365),
    bucket_days: int = Query(default=1, ge=1, le=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> HistoricalQueryResponse:
    """Get historical failure analysis across all failed traces.

    Analysis covers all failed traces (loaded page-by-page internally via the
    shared helper). The ``page`` / ``page_size`` query params report pagination
    over the underlying failed-trace set (3.6).
    """
    all_records = await _load_failure_records(
        trace_repo, blame_engine, page_size=page_size
    )

    total_traces = len(all_records)
    has_more = (page * page_size) < total_traces

    # Analyse
    analyzer = HistoricalAnalyzer()
    analyzer.add_records(all_records)
    report = analyzer.analyse(
        time_window_days=time_window_days,
        bucket_days=bucket_days,
    )

    logger.info(
        "historical_analysis_completed",
        total_failures=report.total_failures,
        trend=str(report.trend),
    )

    return HistoricalQueryResponse(
        report_id=report.report_id,
        total_failures=report.total_failures,
        time_range={
            "start": report.time_range_start.isoformat() if report.time_range_start else None,
            "end": report.time_range_end.isoformat() if report.time_range_end else None,
        },
        frequency=[
            {
                "period_start": f.period_start.isoformat(),
                "period_end": f.period_end.isoformat(),
                "count": f.count,
                "failure_modes": f.failure_modes,
            }
            for f in report.frequency
        ],
        recurring_patterns=[
            {
                "failure_mode": p.failure_mode,
                "step_name": p.step_name,
                "occurrences": p.occurrences,
                "percentage": p.percentage,
                "avg_score": p.avg_score,
                "sample_trace_ids": p.sample_trace_ids,
            }
            for p in report.recurring_patterns
        ],
        trend=str(report.trend),
        trend_confidence=report.trend_confidence,
        correlations=[
            {
                "dimension": c.dimension,
                "description": c.description,
                "strength": c.strength,
                "detail": c.detail,
            }
            for c in report.correlations
        ],
        failure_mode_distribution=report.failure_mode_distribution,
        step_distribution=report.step_distribution,
        avg_score=report.avg_score,
        page=page,
        page_size=page_size,
        total_traces=total_traces,
        has_more=has_more,
    )


@router.get("/trends", response_model=TrendsResponse)
async def get_trends(
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
    time_window_days: int = Query(default=30, ge=1, le=365),
    bucket_days: int = Query(default=1, ge=1, le=30),
) -> TrendsResponse:
    """Get failure trend data as a time series."""
    all_records = await _load_failure_records(trace_repo, blame_engine)

    analyzer = HistoricalAnalyzer()
    analyzer.add_records(all_records)
    report = analyzer.analyse(
        time_window_days=time_window_days,
        bucket_days=bucket_days,
    )

    data_points = [
        TrendDataPoint(
            period=f.period_start.isoformat(),
            count=f.count,
            failure_modes=f.failure_modes,
        )
        for f in report.frequency
    ]

    return TrendsResponse(
        trend=str(report.trend),
        confidence=report.trend_confidence,
        data_points=data_points,
        total_failures=report.total_failures,
    )


# ---------------------------------------------------------------------------
# Real counterfactual re-run (3.1)
# ---------------------------------------------------------------------------


@router.post("/counterfactual/real", response_model=RealRunResponse)
async def run_counterfactual_real(
    body: RealRunRequest,
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
) -> RealRunResponse:
    """Actually re-run the pipeline with a single intervention applied.

    Requires a real :class:`PipelineExecutor` to be wired into the engine
    (e.g. via ``CounterfactualEngine.set_executor``). When none is wired the
    engine falls back to simulation so callers always receive a result.
    The original trace is never mutated.
    """
    if body.trace_id is None and body.trajectory is None:
        raise HTTPException(
            status_code=422, detail="Provide either trace_id or trajectory"
        )

    if body.trace_id is not None:
        record = await trace_repo.get(body.trace_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Trace {body.trace_id} not found")
        trajectory = _reconstruct_trajectory(record)
    else:
        trajectory = _build_inline_trajectory(body.trajectory or {})

    if trajectory.succeeded:
        raise HTTPException(
            status_code=422,
            detail="Counterfactual re-run requires a failed trajectory",
        )

    try:
        change_type = ChangeType(body.change_type)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Unknown change_type: {body.change_type}"
        )

    intervention = Intervention(
        change_type=change_type,
        original_value=body.original_value,
        counterfactual_value=body.counterfactual_value,
        description=body.description or f"Apply {body.change_type}",
    )

    blame = blame_engine.analyse(trajectory)
    engine = CounterfactualEngine(blame_engine=blame_engine)
    result = await engine.run_real(
        trajectory, intervention, blame=blame, timeout_seconds=body.timeout_seconds
    )

    logger.info(
        "counterfactual_real_endpoint",
        trace_id=body.trace_id or "inline",
        change_type=body.change_type,
        improvement_delta=result.improvement_delta,
    )

    return RealRunResponse(**result.to_dict())


# ---------------------------------------------------------------------------
# Recommendation feedback loop (3.2)
# ---------------------------------------------------------------------------


def _build_inline_trajectory(payload: dict[str, Any]) -> Trajectory:
    """Minimal trajectory reconstruction from an inline request payload."""
    raw_steps = payload.get("steps", [])
    steps = [
        TrajectoryStep(
            step_name=s.get("step_name", ""),
            status=StepStatus(s.get("status", "success")),
            payload=s.get("payload", {}),
            metrics=StepMetrics(score=s.get("metrics", {}).get("score")),
        )
        for s in raw_steps
    ]
    return Trajectory(
        run_id=payload.get("trajectory_id", "inline"),
        steps=steps,
        metadata=payload.get("metadata", {}),
    )


async def _persist_applied_recommendation(record: dict[str, Any]) -> None:
    """Persist an applied recommendation (DB when wired, else in-memory)."""
    if _applied_recommendation_repo is not None:
        try:
            await _applied_recommendation_repo.create(record)
            return
        except Exception as exc:
            logger.warning("applied_recommendation_persist_failed", error=str(exc))
    _applied_store[record["id"]] = record


async def _update_applied_outcome(
    recommendation_id: str,
    *,
    outcome_status: str,
    measured_delta: float | None,
    measured_cost_delta: float | None,
    measured_latency_delta_ms: float | None,
    outcome_notes: str,
) -> dict[str, Any] | None:
    """Update the measured outcome for an applied recommendation."""
    if _applied_recommendation_repo is not None:
        try:
            return await _applied_recommendation_repo.update_outcome(
                recommendation_id,
                outcome_status=outcome_status,
                measured_delta=measured_delta,
                measured_cost_delta=measured_cost_delta,
                measured_latency_delta_ms=measured_latency_delta_ms,
                outcome_notes=outcome_notes,
            )
        except Exception as exc:
            logger.warning("applied_recommendation_update_failed", error=str(exc))
            return None

    rec = next(
        (r for r in _applied_store.values() if r.get("recommendation_id") == recommendation_id),
        None,
    )
    if rec is None:
        return None
    rec.update(
        outcome_status=outcome_status,
        measured_delta=measured_delta,
        measured_cost_delta=measured_cost_delta,
        measured_latency_delta_ms=measured_latency_delta_ms,
        outcome_notes=outcome_notes,
    )
    return dict(rec)


async def _list_applied_for_user(
    user_id: str, *, page: int = 1, page_size: int = 50
) -> tuple[list[dict[str, Any]], int]:
    """List applied recommendations for a user (DB when wired, else in-memory)."""
    if _applied_recommendation_repo is not None:
        return await _applied_recommendation_repo.list_for_user(
            user_id, page=page, page_size=page_size
        )
    all_recs = [r for r in _applied_store.values() if r.get("user_id") == user_id]
    total = len(all_recs)
    start = (page - 1) * page_size
    return all_recs[start : start + page_size], total


@router.post("/recommendations/apply", response_model=ApplyRecommendationResponse)
async def apply_recommendation(body: ApplyRecommendationRequest) -> ApplyRecommendationResponse:
    """Record that a recommendation was applied for a trace (feedback loop)."""
    import uuid as _uuid

    rec_id = f"applied-{_uuid.uuid4().hex[:12]}"
    record = {
        "id": rec_id,
        "user_id": body.user_id,
        "trace_id": body.trace_id,
        "recommendation_id": body.recommendation_id,
        "category": body.category,
        "action": body.action,
        "change_type": body.change_type,
        "applied_at": datetime.now(UTC).isoformat(),
        "outcome_status": "pending",
        "measured_delta": None,
        "measured_cost_delta": None,
        "measured_latency_delta_ms": None,
        "outcome_notes": "",
        "metadata": {},
    }
    await _persist_applied_recommendation(record)
    logger.info(
        "recommendation_applied",
        recommendation_id=body.recommendation_id,
        trace_id=body.trace_id,
        user_id=body.user_id,
    )
    return ApplyRecommendationResponse(
        id=rec_id,
        user_id=body.user_id,
        trace_id=body.trace_id,
        recommendation_id=body.recommendation_id,
        category=body.category,
        action=body.action,
        change_type=body.change_type,
        applied_at=record["applied_at"],
        outcome_status="pending",
    )


@router.put(
    "/recommendations/{recommendation_id}/outcome",
    response_model=AppliedRecommendationView,
)
async def update_recommendation_outcome(
    recommendation_id: str, body: OutcomeUpdateRequest
) -> AppliedRecommendationView:
    """Record the measured outcome of an applied recommendation."""
    updated = await _update_applied_outcome(
        recommendation_id,
        outcome_status=body.outcome_status,
        measured_delta=body.measured_delta,
        measured_cost_delta=body.measured_cost_delta,
        measured_latency_delta_ms=body.measured_latency_delta_ms,
        outcome_notes=body.outcome_notes,
    )
    if updated is None:
        raise HTTPException(
            status_code=404, detail=f"Applied recommendation {recommendation_id} not found"
        )
    return AppliedRecommendationView(**updated)


@router.get("/recommendations/applied", response_model=list[AppliedRecommendationView])
async def list_applied_recommendations(
    user_id: str = Query(default="default"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> list[AppliedRecommendationView]:
    """List applied recommendations and their outcomes for a user."""
    items, _ = await _list_applied_for_user(user_id, page=page, page_size=page_size)
    return [AppliedRecommendationView(**item) for item in items]
