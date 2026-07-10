"""Pipeline management routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas import (
    PipelineCreate,
    PipelineListResponse,
    PipelineResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStatus,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/pipelines", tags=["pipelines"])

# ---------------------------------------------------------------------------
# In-memory store (replaced by DB in Phase 6)
# ---------------------------------------------------------------------------

_pipelines: dict[str, dict[str, Any]] = {}
_runs: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_pipeline(body: PipelineCreate) -> PipelineResponse:
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
    _pipelines[pipeline_id] = record
    logger.info("pipeline_created", pipeline_id=pipeline_id, name=body.name)
    return PipelineResponse(**record)


@router.get("", response_model=PipelineListResponse)
async def list_pipelines(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: PipelineStatus | None = None,
    tag: str | None = None,
) -> PipelineListResponse:
    """List pipelines with optional filtering."""
    items = list(_pipelines.values())

    if status is not None:
        items = [p for p in items if p["status"] == status]
    if tag is not None:
        items = [p for p in items if tag in p["tags"]]

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return PipelineListResponse(
        pipelines=[PipelineResponse(**p) for p in page_items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: str) -> PipelineResponse:
    """Get a single pipeline by ID."""
    record = _pipelines.get(pipeline_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")
    return PipelineResponse(**record)


@router.post("/{pipeline_id}/run", response_model=PipelineRunResponse, status_code=202)
async def run_pipeline(pipeline_id: str, body: PipelineRunRequest) -> PipelineRunResponse:
    """Trigger a pipeline execution."""
    record = _pipelines.get(pipeline_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    _runs[run_id] = {
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "status": "queued",
        "config_overrides": body.config_overrides,
        "trace_sample_rate": body.trace_sample_rate,
        "started_at": now,
    }

    logger.info(
        "pipeline_run_triggered",
        pipeline_id=pipeline_id,
        run_id=run_id,
    )
    return PipelineRunResponse(
        run_id=run_id,
        pipeline_id=pipeline_id,
        status="queued",
        started_at=now,
    )


@router.get("/{pipeline_id}/traces")
async def get_pipeline_traces(
    pipeline_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Get all traces for a pipeline."""
    if pipeline_id not in _pipelines:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    # Placeholder — real traces come from the tracer store
    return {
        "pipeline_id": pipeline_id,
        "traces": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }
