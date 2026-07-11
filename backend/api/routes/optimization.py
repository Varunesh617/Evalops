"""Optimization routes — wired to real ConfigSweeper and DB repositories."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_sweep_repository
from backend.api.schemas import (
    CostReportResponse,
    DeploymentHistoryResponse,
    DeploymentRecordResponse,
    DeployRequest,
    DeployResponse,
    ExperimentHistoryResponse,
    ExperimentLogResponse,
    ExperimentSearchRequest,
    ParetoPoint,
    ParetoResponse,
    RollbackRequest,
    SweepRequest,
    SweepStatus,
    SweepStatusResponse,
)
from backend.core.config import PipelineConfig
from backend.core.pipeline import PipelineExecutor
from backend.db.repositories import SweepRepository
from backend.eval.engine import EvalEngine
from backend.eval.models import Step as EvalStep
from backend.eval.models import StepType as EvalStepType
from backend.eval.models import Trajectory as EvalTrajectory
from backend.optimization.auto_deploy import DeployManager
from backend.optimization.cost_tracker import CostTracker
from backend.optimization.experiment_tracker import ExperimentTracker
from backend.optimizer.config_sweeper import (
    ConfigSweeper,
    EvalOutcome,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/optimize", tags=["optimization"])

# Module-level singletons for the optimization subsystems
_experiment_tracker = ExperimentTracker()
_cost_tracker = CostTracker()
_deploy_manager = DeployManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Map core pipeline step names to eval StepType
_STEP_NAME_TO_EVAL_TYPE: dict[str, EvalStepType] = {
    "retrieve": EvalStepType.RETRIEVAL,
    "rerank": EvalStepType.RETRIEVAL,
    "reason": EvalStepType.REASONING,
    "guardrail": EvalStepType.GUARDRAIL_CHECK,
    "generate": EvalStepType.ANSWER,
}


def _core_trajectory_to_eval_trajectory(
    trajectory: Any,
    query: str,
) -> EvalTrajectory:
    """Convert a core tracer Trajectory to an eval.models.Trajectory."""
    eval_steps: list[EvalStep] = []
    for i, step in enumerate(trajectory.steps):
        result_data = step.payload.get("result", {})
        eval_steps.append(
            EvalStep(
                step_id=i,
                step_type=_STEP_NAME_TO_EVAL_TYPE.get(
                    step.step_name, EvalStepType.REASONING
                ),
                input_text=query,
                output_text=str(result_data.get("text", "")),
                context_chunks=result_data.get("documents", []),
                tokens_used=step.tokens.total_tokens,
                cost_usd=result_data.get("cost_usd", 0.0),
                metadata={"core_status": str(step.status), "error": step.error},
            )
        )

    final_answer = ""
    if trajectory.steps:
        last_result = trajectory.steps[-1].payload.get("result", {})
        final_answer = str(last_result.get("text", ""))

    return EvalTrajectory(
        trajectory_id=trajectory.run_id,
        query=query,
        steps=eval_steps,
        final_answer=final_answer,
        total_tokens=trajectory.total_tokens.total_tokens,
        metadata=trajectory.metadata,
    )


def _compute_pareto_frontier(trials: list[dict[str, Any]]) -> list[ParetoPoint]:
    """Extract Pareto-optimal points from trial results (quality vs cost)."""
    if not trials:
        return []

    n = len(trials)
    is_dominated = [False] * n
    for i in range(n):
        if is_dominated[i]:
            continue
        qi = trials[i].get("quality_score", 0.0)
        ci = trials[i].get("cost_usd", 0.0)
        for j in range(n):
            if i == j or is_dominated[j]:
                continue
            qj = trials[j].get("quality_score", 0.0)
            cj = trials[j].get("cost_usd", 0.0)
            # j dominates i: better-or-equal quality AND lower-or-equal cost
            if qj >= qi and cj <= ci and (qj > qi or cj < ci):
                is_dominated[i] = True
                break

    frontier: list[ParetoPoint] = []
    for i in range(n):
        if not is_dominated[i]:
            frontier.append(
                ParetoPoint(
                    params=trials[i].get("params", {}),
                    objectives={
                        "quality_score": trials[i].get("quality_score", 0.0),
                        "cost_usd": trials[i].get("cost_usd", 0.0),
                    },
                    rank=1,
                )
            )
    return frontier


# ---------------------------------------------------------------------------
# Background sweep execution
# ---------------------------------------------------------------------------


async def _execute_sweep_background(
    sweep_id: str,
    pipeline_id: str,
    n_trials: int,
    timeout_seconds: float | None,
    objective: str,
    query: str,
    sweep_repo: SweepRepository,
) -> None:
    """Run ConfigSweeper in the background and persist results."""

    class _SweepEvalFunction:
        """Implements the EvalFunction protocol for ConfigSweeper."""

        def __init__(self, eval_query: str) -> None:
            self._query = eval_query
            self._engine = EvalEngine.default()

        async def __call__(self, config: PipelineConfig) -> EvalOutcome:
            executor = PipelineExecutor(config=config)
            core_trajectory = await executor.execute(self._query)

            eval_trajectory = _core_trajectory_to_eval_trajectory(
                core_trajectory, self._query
            )
            result = await self._engine.run(eval_trajectory)

            quality = result.aggregate_score
            cost = sum(
                s.payload.get("result", {}).get("cost_usd", 0.0)
                for s in core_trajectory.steps
            )
            latency = core_trajectory.latency_ms or 0.0

            return EvalOutcome(
                quality_score=quality,
                cost_usd=cost,
                latency_ms=latency,
            )

    try:
        await sweep_repo.update(sweep_id, {"status": SweepStatus.RUNNING})

        eval_fn = _SweepEvalFunction(query)
        sweeper = ConfigSweeper(
            eval_fn=eval_fn,
            n_trials=n_trials,
            timeout_seconds=timeout_seconds,
            study_name=f"evalops-sweep-{sweep_id}",
        )
        sweep_result = await sweeper.run()

        # Serialize trial results for Pareto extraction
        trial_dicts = [
            {
                "trial_number": t.trial_number,
                "params": t.params,
                "quality_score": t.quality_score,
                "cost_usd": t.cost_usd,
                "latency_ms": t.latency_ms,
                "composite_score": t.composite_score,
            }
            for t in sweep_result.all_trials
        ]

        best_params = sweep_result.best_config.model_dump(mode="json")
        # Strip secrets for storage
        for section in ("retrieval", "reranker", "agent", "generator"):
            section_data = best_params.get(section, {})
            section_data.pop("api_key", None)
            section_data.pop("database_url", None)

        pareto_frontier = _compute_pareto_frontier(trial_dicts)

        await sweep_repo.update(
            sweep_id,
            {
                "status": SweepStatus.COMPLETED,
                "trials_completed": sweep_result.trials_completed,
                "best_value": sweep_result.best_composite_score,
                "best_params": best_params,
                "metadata": {
                    "all_trials": trial_dicts,
                    "pareto_frontier": [p.model_dump() for p in pareto_frontier],
                },
                "completed_at": datetime.now(UTC),
            },
        )

        logger.info(
            "sweep_completed",
            sweep_id=sweep_id,
            trials=sweep_result.trials_completed,
            best_score=round(sweep_result.best_composite_score, 4),
        )
    except Exception:
        logger.exception("sweep_failed", sweep_id=sweep_id)
        await sweep_repo.update(sweep_id, {"status": SweepStatus.FAILED})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sweep", response_model=SweepStatusResponse, status_code=201)
async def start_sweep(
    body: SweepRequest,
    sweep_repo: Annotated[SweepRepository, Depends(get_sweep_repository)],
) -> SweepStatusResponse:
    """Start a hyperparameter configuration sweep via Optuna in the background."""
    sweep_id = f"sweep-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    query = body.search_space.get("query", "What is the capital of France?")

    estimated = now + timedelta(seconds=body.timeout_seconds)

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
        "estimated_completion": estimated,
        "metadata": {},
    }
    await sweep_repo.create(record)

    asyncio.create_task(
        _execute_sweep_background(
            sweep_id=sweep_id,
            pipeline_id=body.pipeline_id,
            n_trials=body.n_trials,
            timeout_seconds=body.timeout_seconds,
            objective=body.objective,
            query=query,
            sweep_repo=sweep_repo,
        ),
    )

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
        estimated_completion=estimated,
    )


@router.get("/status", response_model=SweepStatusResponse)
async def get_sweep_status(
    sweep_repo: Annotated[SweepRepository, Depends(get_sweep_repository)],
    sweep_id: str = Query(..., description="Sweep ID to check"),
) -> SweepStatusResponse:
    """Get the current status of a config sweep from the DB."""
    record = await sweep_repo.get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id} not found")
    return SweepStatusResponse(
        sweep_id=record["sweep_id"],
        pipeline_id=record["pipeline_id"],
        status=record["status"],
        trials_completed=record.get("trials_completed", 0),
        best_value=record.get("best_value"),
        best_params=record.get("best_params", {}),
        started_at=record["started_at"],
        estimated_completion=record.get("estimated_completion"),
    )


@router.get("/pareto", response_model=ParetoResponse)
async def get_pareto_frontier(
    sweep_repo: Annotated[SweepRepository, Depends(get_sweep_repository)],
    sweep_id: str = Query(..., description="Sweep ID"),
) -> ParetoResponse:
    """Get the Pareto-optimal frontier for a completed sweep."""
    record = await sweep_repo.get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id} not found")

    if record["status"] != SweepStatus.COMPLETED:
        raise HTTPException(
            status_code=422,
            detail=f"Sweep {sweep_id} has not completed yet (status={record['status']})",
        )

    stored_frontier = record.get("metadata", {}).get("pareto_frontier", [])
    frontier = [ParetoPoint(**p) for p in stored_frontier]

    # Fallback: compute on-the-fly if stored frontier is empty but trials exist
    if not frontier:
        all_trials = record.get("metadata", {}).get("all_trials", [])
        frontier = _compute_pareto_frontier(all_trials)

    return ParetoResponse(
        sweep_id=sweep_id,
        frontier=frontier,
        total_points=len(frontier),
    )


# ---------------------------------------------------------------------------
# Deployment routes
# ---------------------------------------------------------------------------


@router.post("/deploy", response_model=DeployResponse, status_code=201)
async def deploy_config(body: DeployRequest) -> DeployResponse:
    """Deploy an optimized pipeline configuration."""
    try:
        config = PipelineConfig.model_validate(body.config)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid pipeline config: {exc}",
        )

    candidate = _deploy_manager.evaluate_deployment_candidate(
        pipeline_id=body.pipeline_id,
        config=config,
        quality_score=body.quality_score,
        cost_usd=body.cost_usd,
        latency_ms=body.latency_ms,
        trials_completed=body.trials_completed,
        tests_passed=body.tests_passed,
    )

    if candidate.status != "approved":
        raise HTTPException(status_code=422, detail=candidate.message)

    result = _deploy_manager.deploy_config(
        pipeline_id=body.pipeline_id,
        config=config,
        message=body.message,
    )

    logger.info(
        "config_deployed",
        deployment_id=result.deployment_id,
        pipeline_id=body.pipeline_id,
    )
    return DeployResponse(
        deployment_id=result.deployment_id,
        pipeline_id=result.pipeline_id,
        status=result.status,
        config_diff=result.config_diff,
        previous_config=result.previous_config,
        new_config=result.new_config,
        message=result.message,
        rollback_id=result.rollback_id,
        created_at=result.created_at,
    )


@router.post("/rollback", response_model=DeployResponse)
async def rollback_config(body: RollbackRequest) -> DeployResponse:
    """Rollback a pipeline to its previous configuration."""
    result = _deploy_manager.rollback(body.pipeline_id)

    if result.status == "failed":
        raise HTTPException(status_code=404, detail=result.message)

    logger.info(
        "config_rolled_back",
        deployment_id=result.deployment_id,
        pipeline_id=body.pipeline_id,
    )
    return DeployResponse(
        deployment_id=result.deployment_id,
        pipeline_id=result.pipeline_id,
        status=result.status,
        config_diff=result.config_diff,
        previous_config=result.previous_config,
        new_config=result.new_config,
        message=result.message,
        rollback_id=result.rollback_id,
        created_at=result.created_at,
    )


@router.get("/deployments", response_model=DeploymentHistoryResponse)
async def get_deployments(
    pipeline_id: str = Query(..., description="Pipeline ID"),
    limit: int = Query(default=20, ge=1, le=100),
) -> DeploymentHistoryResponse:
    """List deployment history for a pipeline."""
    records = _deploy_manager.get_deployment_history(pipeline_id, limit=limit)
    return DeploymentHistoryResponse(
        pipeline_id=pipeline_id,
        deployments=[
            DeploymentRecordResponse(
                deployment_id=r.deployment_id,
                pipeline_id=r.pipeline_id,
                status=r.status,
                message=r.message,
                created_at=r.created_at,
            )
            for r in records
        ],
        total=len(records),
    )


# ---------------------------------------------------------------------------
# Cost routes
# ---------------------------------------------------------------------------


@router.get("/costs", response_model=CostReportResponse)
async def get_costs(
    pipeline_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    forecast_days: int = Query(default=7, ge=1, le=30),
) -> CostReportResponse:
    """Get cost breakdown with forecasts and anomaly detection."""
    report = _cost_tracker.get_report(
        pipeline_id=pipeline_id,
        model=model,
        user_id=user_id,
        days=days,
        forecast_days=forecast_days,
    )
    return CostReportResponse(
        total_cost_usd=report.total_cost_usd,
        total_entries=report.total_entries,
        period_start=report.period_start,
        period_end=report.period_end,
        by_pipeline=[
            {"label": b.label, "total_cost_usd": b.total_cost_usd, "entry_count": b.entry_count,
             "avg_cost_usd": b.avg_cost_usd, "avg_latency_ms": b.avg_latency_ms,
             "total_tokens": b.total_tokens}
            for b in report.by_pipeline
        ],
        by_model=[
            {"label": b.label, "total_cost_usd": b.total_cost_usd, "entry_count": b.entry_count,
             "avg_cost_usd": b.avg_cost_usd, "avg_latency_ms": b.avg_latency_ms,
             "total_tokens": b.total_tokens}
            for b in report.by_model
        ],
        by_user=[
            {"label": b.label, "total_cost_usd": b.total_cost_usd, "entry_count": b.entry_count,
             "avg_cost_usd": b.avg_cost_usd, "avg_latency_ms": b.avg_latency_ms,
             "total_tokens": b.total_tokens}
            for b in report.by_user
        ],
        daily_costs=report.daily_costs,
        forecasts=[
            {"period_start": f.period_start, "period_end": f.period_end,
             "projected_cost_usd": f.projected_cost_usd, "confidence": f.confidence}
            for f in report.forecasts
        ],
        anomalies=[
            {"entry_id": a.entry_id, "pipeline_id": a.pipeline_id, "model": a.model,
             "cost_usd": a.cost_usd, "expected_cost_usd": a.expected_cost_usd,
             "deviation_ratio": a.deviation_ratio}
            for a in report.anomalies
        ],
    )


# ---------------------------------------------------------------------------
# Experiment history routes
# ---------------------------------------------------------------------------


@router.get("/experiments", response_model=ExperimentHistoryResponse)
async def get_experiments(
    pipeline_id: str = Query(..., description="Pipeline ID"),
    limit: int = Query(default=20, ge=1, le=100),
) -> ExperimentHistoryResponse:
    """Get experiment history for a pipeline."""
    experiments = _experiment_tracker.get_experiment_history(pipeline_id, limit=limit)
    return ExperimentHistoryResponse(
        pipeline_id=pipeline_id,
        experiments=[
            ExperimentLogResponse(
                experiment_id=e.experiment_id,
                pipeline_id=e.pipeline_id,
                run_name=e.run_name,
                params=e.params,
                metrics=e.metrics,
                tags=e.tags,
                source=e.source,
                created_at=e.created_at,
            )
            for e in experiments
        ],
        total=len(experiments),
    )


@router.post("/experiments/search", response_model=ExperimentHistoryResponse)
async def search_experiments(
    body: ExperimentSearchRequest,
) -> ExperimentHistoryResponse:
    """Search experiments by metrics, tags, and date range."""
    experiments = _experiment_tracker.search_experiments(
        min_metrics=body.min_metrics,
        tags=body.tags,
        after=body.after,
        before=body.before,
        limit=body.limit,
    )
    pipeline_id = body.pipeline_id or "(all)"
    return ExperimentHistoryResponse(
        pipeline_id=pipeline_id,
        experiments=[
            ExperimentLogResponse(
                experiment_id=e.experiment_id,
                pipeline_id=e.pipeline_id,
                run_name=e.run_name,
                params=e.params,
                metrics=e.metrics,
                tags=e.tags,
                source=e.source,
                created_at=e.created_at,
            )
            for e in experiments
        ],
        total=len(experiments),
    )
