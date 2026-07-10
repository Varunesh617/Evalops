"""Optimization routes — config sweeps, Pareto frontier, status."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas import (
    ParetoPoint,
    ParetoResponse,
    SweepRequest,
    SweepStatus,
    SweepStatusResponse,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/optimize", tags=["optimization"])

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_sweeps: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sweep", response_model=SweepStatusResponse, status_code=201)
async def start_sweep(body: SweepRequest) -> SweepStatusResponse:
    """Start a hyperparameter configuration sweep via Optuna."""
    sweep_id = f"sweep-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    record: dict[str, Any] = {
        "sweep_id": sweep_id,
        "pipeline_id": body.pipeline_id,
        "status": SweepStatus.PENDING,
        "trials_completed": 0,
        "best_value": None,
        "best_params": {},
        "search_space": body.search_space,
        "objective": body.objective,
        "n_trials": body.n_trials,
        "timeout_seconds": body.timeout_seconds,
        "started_at": now,
        "estimated_completion": None,
    }
    _sweeps[sweep_id] = record

    logger.info(
        "sweep_started",
        sweep_id=sweep_id,
        pipeline_id=body.pipeline_id,
        n_trials=body.n_trials,
    )
    return SweepStatusResponse(
        sweep_id=sweep_id,
        pipeline_id=body.pipeline_id,
        status=SweepStatus.PENDING,
        trials_completed=0,
        best_value=None,
        best_params={},
        started_at=now,
        estimated_completion=None,
    )


@router.get("/status", response_model=SweepStatusResponse)
async def get_sweep_status(
    sweep_id: str = Query(..., description="Sweep ID to check"),
) -> SweepStatusResponse:
    """Get the current status of a config sweep."""
    record = _sweeps.get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id} not found")
    return SweepStatusResponse(
        sweep_id=record["sweep_id"],
        pipeline_id=record["pipeline_id"],
        status=record["status"],
        trials_completed=record["trials_completed"],
        best_value=record["best_value"],
        best_params=record["best_params"],
        started_at=record["started_at"],
        estimated_completion=record.get("estimated_completion"),
    )


@router.get("/pareto", response_model=ParetoResponse)
async def get_pareto_frontier(
    sweep_id: str = Query(..., description="Sweep ID"),
) -> ParetoResponse:
    """Get the Pareto-optimal frontier for a completed sweep."""
    record = _sweeps.get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id} not found")

    if record["status"] != SweepStatus.COMPLETED:
        raise HTTPException(
            status_code=422,
            detail=f"Sweep {sweep_id} has not completed yet (status={record['status']})",
        )

    # Placeholder: real Pareto extraction plugs in here
    frontier: list[ParetoPoint] = [
        ParetoPoint(
            params=record["best_params"],
            objectives={record["objective"]: record["best_value"] or 0.0},
            rank=1,
        )
    ]

    return ParetoResponse(
        sweep_id=sweep_id,
        frontier=frontier,
        total_points=len(frontier),
    )
