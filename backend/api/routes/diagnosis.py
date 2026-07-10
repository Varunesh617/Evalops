"""Diagnosis routes — counterfactual analysis, recommendations, and historical trends."""

from __future__ import annotations

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
from backend.db.repositories import TraceRepository
from backend.diagnosis.counterfactual import CounterfactualEngine, CounterfactualReport
from backend.diagnosis.historical_analyzer import (
    FailureRecord,
    HistoricalAnalyzer,
    HistoricalReport,
    TrendDirection,
)
from backend.diagnosis.recommender import RecommendationEngine, RecommendationReport
from backend.eval.blame_attribution import BlameAttributionEngine

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])


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
) -> HistoricalQueryResponse:
    """Get historical failure analysis across all failed traces."""
    # Load all failed traces (paginated internally to avoid OOM)
    all_records: list[FailureRecord] = []
    page = 1
    page_size = 100

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
                all_records.append(
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
    )


@router.get("/trends", response_model=TrendsResponse)
async def get_trends(
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
    time_window_days: int = Query(default=30, ge=1, le=365),
    bucket_days: int = Query(default=1, ge=1, le=30),
) -> TrendsResponse:
    """Get failure trend data as a time series."""
    all_records: list[FailureRecord] = []
    page = 1
    page_size = 100

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
                all_records.append(
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
                    "failed_to_process_trace_for_trend",
                    trace_id=item.get("id"),
                    exc_info=True,
                )

        if page * page_size >= total:
            break
        page += 1

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
