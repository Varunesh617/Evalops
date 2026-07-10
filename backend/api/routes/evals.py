"""Evaluation routes — run, retrieve, and compare evals using the real EvalEngine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_eval_engine, get_eval_repository
from backend.api.schemas import (
    EvalCompareResponse,
    EvalResultResponse,
    EvalRunRequest,
)
from backend.eval.engine import EvalEngine
from backend.eval.metrics import METRIC_REGISTRY
from backend.eval.models import Trajectory as EvalTrajectory
from backend.db.repositories import EvalRepository

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/evals", tags=["evals"])

AVAILABLE_METRICS = set(METRIC_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=EvalResultResponse, status_code=201)
async def run_eval(
    body: EvalRunRequest,
    eval_repo: Annotated[EvalRepository, Depends(get_eval_repository)],
    engine: Annotated[EvalEngine, Depends(get_eval_engine)],
) -> EvalResultResponse:
    """Run an evaluation on a trajectory using the real EvalEngine."""
    invalid = [m for m in body.metrics if m not in AVAILABLE_METRICS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown metrics: {', '.join(invalid)}. "
            f"Available: {', '.join(sorted(AVAILABLE_METRICS))}",
        )

    trajectory_data = body.trajectory
    trajectory_id = trajectory_data.get(
        "trajectory_id", f"traj-{uuid.uuid4().hex[:12]}"
    )

    # Build an engine scoped to the requested metrics
    scoped_engine = EvalEngine.from_names(body.metrics)

    # Parse the trajectory dict into the eval Trajectory model
    try:
        eval_trajectory = EvalTrajectory(**trajectory_data)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid trajectory payload: {exc}",
        )

    # Run the real engine
    eval_result = await scoped_engine.run(eval_trajectory)

    eval_id = f"eval-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    scores = eval_result.scores
    agg = eval_result.aggregate_score

    # Serialise metric_details for storage
    metric_details = [
        {
            "metric_name": mr.metric_name,
            "overall_score": mr.overall_score,
            "details": mr.details,
            "step_count": len(mr.step_scores),
        }
        for mr in eval_result.metric_results
    ]

    record = {
        "id": eval_id,
        "trajectory_id": trajectory_id,
        "scores": scores,
        "aggregate_score": agg,
        "metric_details": metric_details,
        "status": "completed",
        "created_at": now,
    }
    await eval_repo.create(record)

    logger.info(
        "eval_completed",
        eval_id=eval_id,
        trajectory_id=trajectory_id,
        aggregate_score=agg,
    )
    return EvalResultResponse(**record)


@router.get("/{eval_id}", response_model=EvalResultResponse)
async def get_eval(
    eval_id: str,
    eval_repo: Annotated[EvalRepository, Depends(get_eval_repository)],
) -> EvalResultResponse:
    """Retrieve a single evaluation result from the DB."""
    record = await eval_repo.get(eval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Eval {eval_id} not found")
    return EvalResultResponse(**record)


@router.get("/compare", response_model=EvalCompareResponse)
async def compare_evals(
    eval_repo: Annotated[EvalRepository, Depends(get_eval_repository)],
    eval_a: str = Query(..., description="First eval ID"),
    eval_b: str = Query(..., description="Second eval ID"),
) -> EvalCompareResponse:
    """Compare two evaluation results side-by-side."""
    rec_a = await eval_repo.get(eval_a)
    rec_b = await eval_repo.get(eval_b)

    if rec_a is None:
        raise HTTPException(status_code=404, detail=f"Eval {eval_a} not found")
    if rec_b is None:
        raise HTTPException(status_code=404, detail=f"Eval {eval_b} not found")

    a_resp = EvalResultResponse(**rec_a)
    b_resp = EvalResultResponse(**rec_b)

    diffs: dict[str, float] = {}
    all_keys = set(a_resp.scores) | set(b_resp.scores)
    for key in all_keys:
        diffs[key] = round(
            b_resp.scores.get(key, 0.0) - a_resp.scores.get(key, 0.0), 4
        )

    winner: str | None = None
    if a_resp.aggregate_score > b_resp.aggregate_score:
        winner = a_resp.id
    elif b_resp.aggregate_score > a_resp.aggregate_score:
        winner = b_resp.id

    return EvalCompareResponse(
        eval_a=a_resp,
        eval_b=b_resp,
        score_diffs=diffs,
        winner=winner,
    )
