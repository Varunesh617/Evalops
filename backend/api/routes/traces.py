"""Trace inspection routes — list, detail, and blame attribution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas import (
    BlameAttribution,
    TraceListResponse,
    TraceResponse,
    TraceStatus,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/traces", tags=["traces"])

# ---------------------------------------------------------------------------
# In-memory store (populated by pipeline runs + tracer)
# ---------------------------------------------------------------------------

_traces: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=TraceListResponse)
async def list_traces(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    pipeline_id: str | None = None,
    status: TraceStatus | None = None,
    min_cost: float | None = Query(default=None, ge=0.0),
    max_cost: float | None = Query(default=None, ge=0.0),
) -> TraceListResponse:
    """List traces with optional filters for pipeline, status, and cost."""
    items = list(_traces.values())

    if pipeline_id is not None:
        items = [t for t in items if t["pipeline_id"] == pipeline_id]
    if status is not None:
        items = [t for t in items if t["status"] == status]
    if min_cost is not None:
        items = [t for t in items if t["total_cost_usd"] >= min_cost]
    if max_cost is not None:
        items = [t for t in items if t["total_cost_usd"] <= max_cost]

    # Sort newest-first
    items.sort(key=lambda t: t["started_at"], reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return TraceListResponse(
        traces=[TraceResponse(**t) for t in page_items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{trace_id}", response_model=TraceResponse)
async def get_trace(trace_id: str) -> TraceResponse:
    """Get full detail for a single trace."""
    record = _traces.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return TraceResponse(**record)


@router.get("/{trace_id}/blame", response_model=BlameAttribution)
async def get_trace_blame(trace_id: str) -> BlameAttribution:
    """Get blame attribution for a failed trace.

    Analyses the trajectory to identify the root-cause step and contributing
    factors. Returns 404 if the trace does not exist, or 422 if the trace did
    not fail.
    """
    record = _traces.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

    if record["status"] != TraceStatus.FAILED:
        raise HTTPException(
            status_code=422,
            detail="Blame attribution is only available for failed traces",
        )

    # Placeholder: plug in real blame_attribution engine here
    attribution = BlameAttribution(
        trace_id=trace_id,
        failure_step=0,
        failure_type="unknown",
        confidence=0.0,
        root_cause="Attribution engine not yet connected",
        contributing_factors=[],
        suggested_fixes=[],
    )

    logger.info("blame_retrieved", trace_id=trace_id)
    return attribution
