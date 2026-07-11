"""Trace inspection routes — list, detail, and real blame attribution."""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_blame_engine, get_trace_repository
from backend.api.schemas import (
    BlameAttribution,
    TraceListResponse,
    TraceResponse,
    TraceStatus,
)
from backend.core.config import StepStatus
from backend.core.tracer import (
    StepMetrics,
    TokenUsage,
    Trajectory,
    TrajectoryStep,
)
from backend.db.repositories import TraceRepository
from backend.eval.blame_attribution import BlameAttributionEngine

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/traces", tags=["traces"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trace_to_response(record: dict[str, Any]) -> TraceResponse:
    """Convert a stored trace record to the API response schema."""
    raw_steps: list[dict[str, Any]] = record.get("steps", [])
    steps = [
        {
            "step_id": i,
            "step_type": s.get("step_name", "unknown"),
            "input_text": s.get("payload", {}).get("query", ""),
            "output_text": str(
                s.get("payload", {}).get("result", {}).get("text", "")
            ),
            "tokens_used": s.get("tokens", {}).get("total_tokens", 0),
            "cost_usd": s.get("payload", {}).get("cost_usd", 0.0),
            "duration_ms": s.get("latency_ms") or 0.0,
            "status": s.get("status", "success"),
            "metadata": s.get("metrics", {}).get("metadata", {}),
        }
        for i, s in enumerate(raw_steps)
    ]

    total_tokens_raw = record.get("total_tokens", 0)
    if isinstance(total_tokens_raw, dict):
        total_tokens = total_tokens_raw.get("total_tokens", 0)
    else:
        total_tokens = total_tokens_raw

    return TraceResponse(
        id=record["id"],
        pipeline_id=record.get("pipeline_id", ""),
        query=record.get("query", ""),
        status=TraceStatus(record.get("status", "completed")),
        steps=steps,
        total_tokens=total_tokens,
        total_cost_usd=record.get("total_cost_usd", 0.0),
        started_at=record["started_at"],
        completed_at=record.get("completed_at"),
        metadata=record.get("metadata", {}),
    )


def _reconstruct_core_trajectory(record: dict[str, Any]) -> Trajectory:
    """Reconstruct a core Trajectory from a stored trace record.

    This is needed so the BlameAttributionEngine can analyse the trajectory
    using its native dataclass representation.
    """
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


def _blame_report_to_schema(
    trace_id: str,
    trajectory: Trajectory,
    report: Any,
) -> BlameAttribution:
    """Map a BlameReport to the API BlameAttribution schema."""
    # Find the index of the first failed step
    failure_step = 0
    for i, s in enumerate(trajectory.steps):
        if s.status != StepStatus.SUCCESS:
            failure_step = i
            break

    contributing_factors = [
        f"{c.step_name}: {c.message}" for c in report.cascade_chain if c.propagated
    ]

    return BlameAttribution(
        trace_id=trace_id,
        failure_step=failure_step,
        failure_type=str(report.root_cause_mode),
        confidence=min(1.0, max(0.0, report.score)),
        root_cause=report.root_cause_message,
        contributing_factors=contributing_factors,
        suggested_fixes=report.remediation,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=TraceListResponse)
async def list_traces(
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    pipeline_id: str | None = None,
    status: TraceStatus | None = None,
    min_cost: float | None = Query(default=None, ge=0.0),
    max_cost: float | None = Query(default=None, ge=0.0),
) -> TraceListResponse:
    """List traces with optional filters for pipeline, status, and cost."""
    items, total = await trace_repo.list(
        pipeline_id=pipeline_id,
        status=status.value if status else None,
        min_cost=min_cost,
        max_cost=max_cost,
        page=page,
        page_size=page_size,
    )

    return TraceListResponse(
        traces=[_trace_to_response(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{trace_id}", response_model=TraceResponse)
async def get_trace(
    trace_id: str,
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
) -> TraceResponse:
    """Get full detail for a single trace."""
    record = await trace_repo.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return _trace_to_response(record)


@router.get("/{trace_id}/blame", response_model=BlameAttribution)
async def get_trace_blame(
    trace_id: str,
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    blame_engine: Annotated[BlameAttributionEngine, Depends(get_blame_engine)],
) -> BlameAttribution:
    """Run the real BlameAttributionEngine on a failed trace."""
    record = await trace_repo.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

    if record.get("status") != TraceStatus.FAILED:
        raise HTTPException(
            status_code=422,
            detail="Blame attribution is only available for failed traces",
        )

    # Reconstruct the core Trajectory from stored data
    trajectory = _reconstruct_core_trajectory(record)

    # Run the real blame attribution engine
    report = blame_engine.analyse(trajectory)

    attribution = _blame_report_to_schema(trace_id, trajectory, report)

    logger.info(
        "blame_retrieved",
        trace_id=trace_id,
        root_cause=report.root_cause_step,
        failure_mode=str(report.root_cause_mode),
        severity=str(report.severity),
    )
    return attribution
