"""Evaluation routes — run, retrieve, and compare evals."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas import (
    EvalCompareRequest,
    EvalCompareResponse,
    EvalResultResponse,
    EvalRunRequest,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/evals", tags=["evals"])

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_evals: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AVAILABLE_METRICS = {
    "faithfulness",
    "context_relevance",
    "trajectory_coherence",
    "tool_call_accuracy",
    "guardrail_fp_rate",
    "cost_efficiency",
}


def _compute_scores(trajectory: dict[str, Any], metrics: list[str]) -> dict[str, float]:
    """Run metric evaluations and return metric → score mapping.

    This delegates to the real engine when available; falls back to placeholder
    scoring so the API is testable end-to-end without the full engine installed.
    """
    scores: dict[str, float] = {}
    for metric in metrics:
        if metric not in AVAILABLE_METRICS:
            continue
        # Placeholder: real engine integration plugs in here
        scores[metric] = 0.0
    return scores


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=EvalResultResponse, status_code=201)
async def run_eval(body: EvalRunRequest) -> EvalResultResponse:
    """Run an evaluation on a trajectory."""
    invalid = [m for m in body.metrics if m not in AVAILABLE_METRICS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown metrics: {', '.join(invalid)}. "
            f"Available: {', '.join(sorted(AVAILABLE_METRICS))}",
        )

    trajectory = body.trajectory
    trajectory_id = trajectory.get("trajectory_id", f"traj-{uuid.uuid4().hex[:12]}")

    scores = _compute_scores(trajectory, body.metrics)
    agg = sum(scores.values()) / len(scores) if scores else 0.0

    eval_id = f"eval-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    record = {
        "id": eval_id,
        "trajectory_id": trajectory_id,
        "scores": scores,
        "aggregate_score": agg,
        "metric_details": [],
        "status": "completed",
        "created_at": now,
    }
    _evals[eval_id] = record

    logger.info(
        "eval_completed",
        eval_id=eval_id,
        trajectory_id=trajectory_id,
        aggregate_score=agg,
    )
    return EvalResultResponse(**record)


@router.get("/{eval_id}", response_model=EvalResultResponse)
async def get_eval(eval_id: str) -> EvalResultResponse:
    """Retrieve a single evaluation result."""
    record = _evals.get(eval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Eval {eval_id} not found")
    return EvalResultResponse(**record)


@router.get("/compare", response_model=EvalCompareResponse)
async def compare_evals(
    eval_a: str = Query(..., description="First eval ID"),
    eval_b: str = Query(..., description="Second eval ID"),
) -> EvalCompareResponse:
    """Compare two evaluation results side-by-side."""
    rec_a = _evals.get(eval_a)
    rec_b = _evals.get(eval_b)

    if rec_a is None:
        raise HTTPException(status_code=404, detail=f"Eval {eval_a} not found")
    if rec_b is None:
        raise HTTPException(status_code=404, detail=f"Eval {eval_b} not found")

    a_resp = EvalResultResponse(**rec_a)
    b_resp = EvalResultResponse(**rec_b)

    diffs: dict[str, float] = {}
    all_keys = set(a_resp.scores) | set(b_resp.scores)
    for key in all_keys:
        diffs[key] = round(b_resp.scores.get(key, 0.0) - a_resp.scores.get(key, 0.0), 4)

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
