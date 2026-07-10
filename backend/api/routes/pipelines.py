"""Pipeline management routes — wired to PipelineExecutor and DB repositories."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_pipeline_repository, get_trace_repository
from backend.api.schemas import (
    PipelineCreate,
    PipelineListResponse,
    PipelineResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStatus,
    TraceStatus,
)
from backend.core.config import PipelineConfig
from backend.core.pipeline import PipelineExecutor
from backend.core.tracer import TokenUsage, TrajectoryStep
from backend.db.repositories import PipelineRepository, TraceRepository

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/pipelines", tags=["pipelines"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_trajectory_step(step: TrajectoryStep) -> dict[str, Any]:
    """Serialize a core TrajectoryStep to a storable dict."""
    return {
        "step_id": step.step_id,
        "step_name": step.step_name,
        "status": str(step.status),
        "start_time": step.start_time,
        "end_time": step.end_time,
        "latency_ms": step.latency_ms,
        "tokens": {
            "prompt_tokens": step.tokens.prompt_tokens,
            "completion_tokens": step.tokens.completion_tokens,
            "total_tokens": step.tokens.total_tokens,
        },
        "metrics": {
            "score": step.metrics.score,
            "confidence": step.metrics.confidence,
            "metadata": step.metrics.metadata,
        },
        "error": step.error,
        "error_type": step.error_type,
        "payload": step.payload,
    }


# ---------------------------------------------------------------------------
# Background execution
# ---------------------------------------------------------------------------


async def _execute_pipeline_background(
    run_id: str,
    pipeline_id: str,
    config: dict[str, Any],
    query: str,
    trace_repo: TraceRepository,
    pipeline_repo: PipelineRepository,
    trace_sample_rate: float,
) -> None:
    """Run the pipeline in the background and persist the resulting trace."""
    trace_id = f"tr-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    try:
        await pipeline_repo.update(pipeline_id, {"status": PipelineStatus.RUNNING})
        await trace_repo.create(
            {
                "id": trace_id,
                "pipeline_id": pipeline_id,
                "run_id": run_id,
                "query": query,
                "status": TraceStatus.RUNNING,
                "steps": [],
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "started_at": now,
                "completed_at": None,
                "metadata": {"run_id": run_id},
            }
        )

        pipeline_config = PipelineConfig(
            pipeline_id=pipeline_id,
            trace_sample_rate=trace_sample_rate,
            **config,
        )
        executor = PipelineExecutor(config=pipeline_config)
        trajectory = await executor.execute(query)

        serialized_steps = [_serialize_trajectory_step(s) for s in trajectory.steps]
        total_cost = sum(
            s.payload.get("cost_usd", 0.0) for s in trajectory.steps
        )
        trace_status = (
            TraceStatus.COMPLETED if trajectory.succeeded else TraceStatus.FAILED
        )

        await trace_repo.update(
            trace_id,
            {
                "status": trace_status,
                "steps": serialized_steps,
                "total_tokens": trajectory.total_tokens.total_tokens,
                "total_cost_usd": total_cost,
                "completed_at": datetime.now(UTC),
                "metadata": {
                    **trajectory.metadata,
                    "run_id": run_id,
                    "latency_ms": trajectory.latency_ms,
                },
            },
        )
        await pipeline_repo.update(pipeline_id, {"status": PipelineStatus.COMPLETED})

        logger.info(
            "pipeline_run_completed",
            run_id=run_id,
            trace_id=trace_id,
            succeeded=trajectory.succeeded,
        )
    except Exception:
        logger.exception("pipeline_run_failed", run_id=run_id)
        try:
            await trace_repo.update(
                trace_id,
                {"status": TraceStatus.FAILED, "completed_at": datetime.now(UTC)},
            )
        except Exception:
            logger.exception("trace_update_after_failure_failed", trace_id=trace_id)
        try:
            await pipeline_repo.update(pipeline_id, {"status": PipelineStatus.FAILED})
        except Exception:
            logger.exception("pipeline_update_after_failure_failed", pipeline_id=pipeline_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    repo: Annotated[PipelineRepository, Depends(get_pipeline_repository)],
) -> PipelineResponse:
    """Create a new pipeline configuration."""
    now = datetime.now(UTC)
    pipeline_id = f"pl-{uuid.uuid4().hex[:12]}"

    record = {
        "id": pipeline_id,
        "name": body.name,
        "description": body.description,
        "config": body.config,
        "status": PipelineStatus.DRAFT,
        "tags": body.tags,
        "created_at": now,
        "updated_at": now,
    }
    await repo.create(record)
    logger.info("pipeline_created", pipeline_id=pipeline_id, name=body.name)
    return PipelineResponse(**record)


@router.get("", response_model=PipelineListResponse)
async def list_pipelines(
    repo: Annotated[PipelineRepository, Depends(get_pipeline_repository)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: PipelineStatus | None = None,
    tag: str | None = None,
) -> PipelineListResponse:
    """List pipelines with optional filtering."""
    items, total = await repo.list(
        status=status.value if status else None,
        tag=tag,
        page=page,
        page_size=page_size,
    )
    return PipelineListResponse(
        pipelines=[PipelineResponse(**p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: str,
    repo: Annotated[PipelineRepository, Depends(get_pipeline_repository)],
) -> PipelineResponse:
    """Get a single pipeline by ID."""
    record = await repo.get(pipeline_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")
    return PipelineResponse(**record)


@router.post("/{pipeline_id}/run", response_model=PipelineRunResponse, status_code=202)
async def run_pipeline(
    pipeline_id: str,
    body: PipelineRunRequest,
    pipeline_repo: Annotated[PipelineRepository, Depends(get_pipeline_repository)],
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
) -> PipelineRunResponse:
    """Trigger a pipeline execution in the background via PipelineExecutor."""
    record = await pipeline_repo.get(pipeline_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    base_config = record.get("config", {})
    merged_config = {**base_config, **body.config_overrides}
    query = merged_config.pop("query", "What is the capital of France?")

    asyncio.create_task(
        _execute_pipeline_background(
            run_id=run_id,
            pipeline_id=pipeline_id,
            config=merged_config,
            query=query,
            trace_repo=trace_repo,
            pipeline_repo=pipeline_repo,
            trace_sample_rate=body.trace_sample_rate,
        ),
    )

    logger.info("pipeline_run_triggered", pipeline_id=pipeline_id, run_id=run_id)
    return PipelineRunResponse(
        run_id=run_id,
        pipeline_id=pipeline_id,
        status="queued",
        started_at=now,
    )


@router.get("/{pipeline_id}/traces")
async def get_pipeline_traces(
    pipeline_id: str,
    pipeline_repo: Annotated[PipelineRepository, Depends(get_pipeline_repository)],
    trace_repo: Annotated[TraceRepository, Depends(get_trace_repository)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Get all traces for a pipeline from the trace repository."""
    pipeline = await pipeline_repo.get(pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    traces, total = await trace_repo.list(
        pipeline_id=pipeline_id, page=page, page_size=page_size
    )
    return {
        "pipeline_id": pipeline_id,
        "traces": traces,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
