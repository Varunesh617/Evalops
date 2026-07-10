"""MLflow experiment tracking with local JSON fallback.

Wraps MLflow to log pipeline configs, evaluation scores, cost, and latency
for each optimization experiment and trial. Falls back to local JSON storage
when MLflow is not available.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature-detect MLflow
# ---------------------------------------------------------------------------

try:
    import mlflow
    import mlflow.tracking

    _MLFLOW_AVAILABLE = True
except ImportError:
    _MLFLOW_AVAILABLE = False

_FALLBACK_DIR = Path("data/experiments")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TrialLog(BaseModel):
    """Logged data for a single optimization trial."""

    trial_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    trial_number: int
    params: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    artifact_uri: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentLog(BaseModel):
    """Logged data for a full experiment run."""

    experiment_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    pipeline_id: str
    run_name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    trials: list[TrialLog] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = "mlflow"  # or "local"


class ExperimentComparison(BaseModel):
    """Side-by-side comparison of two experiments."""

    experiment_a: ExperimentLog
    experiment_b: ExperimentLog
    metric_diffs: dict[str, float] = Field(default_factory=dict)
    param_diffs: dict[str, Any] = Field(default_factory=dict)
    better_experiment: str | None = None
    primary_metric: str = "composite_score"


# ---------------------------------------------------------------------------
# MLflow-backed tracker
# ---------------------------------------------------------------------------


class ExperimentTracker:
    """Track optimization experiments via MLflow with local JSON fallback.

    Usage::

        tracker = ExperimentTracker()
        exp = tracker.log_experiment(pipeline_id="p-1", ...)
        tracker.log_trial(experiment_id=exp.experiment_id, trial_number=0, ...)
        history = tracker.get_experiment_history("p-1")
    """

    def __init__(
        self,
        tracking_uri: str | None = None,
        experiment_prefix: str = "evalops",
        local_fallback_dir: Path | str = _FALLBACK_DIR,
    ) -> None:
        self._use_mlflow = _MLFLOW_AVAILABLE and tracking_uri is not None
        self._prefix = experiment_prefix
        self._local_dir = Path(local_fallback_dir)
        self._mlflow_client: Any = None

        if self._use_mlflow:
            mlflow.set_tracking_uri(tracking_uri)
            self._mlflow_client = mlflow.tracking.MlflowClient()
            logger.info("experiment_tracker.mlflow_connected", uri=tracking_uri)
        else:
            self._local_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                "experiment_tracker.local_fallback",
                dir=str(self._local_dir),
                mlflow_available=_MLFLOW_AVAILABLE,
            )

    # -- Core logging -------------------------------------------------------

    def log_experiment(
        self,
        *,
        pipeline_id: str,
        run_name: str = "",
        params: dict[str, Any] | None = None,
        metrics: dict[str, float] | None = None,
        tags: dict[str, str] | None = None,
    ) -> ExperimentLog:
        """Log a new experiment run and return its record."""
        now = datetime.now(UTC)
        if not run_name:
            run_name = f"{pipeline_id}-{now.strftime('%Y%m%d-%H%M%S')}"

        log = ExperimentLog(
            pipeline_id=pipeline_id,
            run_name=run_name,
            params=params or {},
            metrics=metrics or {},
            tags=tags or {},
            source="mlflow" if self._use_mlflow else "local",
        )

        if self._use_mlflow:
            self._log_experiment_mlflow(log)
        else:
            self._persist_local(log)

        logger.info(
            "experiment_tracker.experiment_logged",
            experiment_id=log.experiment_id,
            pipeline_id=pipeline_id,
            source=log.source,
        )
        return log

    def log_trial(
        self,
        *,
        experiment_id: str,
        trial_number: int,
        params: dict[str, Any] | None = None,
        metrics: dict[str, float] | None = None,
        tags: dict[str, str] | None = None,
    ) -> TrialLog:
        """Log an individual trial within an experiment."""
        trial = TrialLog(
            trial_number=trial_number,
            params=params or {},
            metrics=metrics or {},
            tags=tags or {},
        )

        if self._use_mlflow:
            self._log_trial_mlflow(experiment_id, trial)
        else:
            self._append_trial_local(experiment_id, trial)

        logger.info(
            "experiment_tracker.trial_logged",
            experiment_id=experiment_id,
            trial_number=trial_number,
            source="mlflow" if self._use_mlflow else "local",
        )
        return trial

    # -- Queries ------------------------------------------------------------

    def compare_experiments(
        self,
        experiment_id_a: str,
        experiment_id_b: str,
        *,
        primary_metric: str = "composite_score",
    ) -> ExperimentComparison:
        """Compare two experiments side-by-side."""
        exp_a = self._get_experiment_local(experiment_id_a)
        exp_b = self._get_experiment_local(experiment_id_b)

        if exp_a is None and self._use_mlflow:
            exp_a = self._fetch_experiment_mlflow(experiment_id_a)
        if exp_b is None and self._use_mlflow:
            exp_b = self._fetch_experiment_mlflow(experiment_id_b)

        if exp_a is None or exp_b is None:
            missing = experiment_id_a if exp_a is None else experiment_id_b
            raise ValueError(f"Experiment {missing} not found")

        metric_diffs: dict[str, float] = {}
        all_keys = set(exp_a.metrics) | set(exp_b.metrics)
        for key in all_keys:
            val_a = exp_a.metrics.get(key, 0.0)
            val_b = exp_b.metrics.get(key, 0.0)
            metric_diffs[key] = val_b - val_a

        param_diffs: dict[str, Any] = {}
        all_param_keys = set(exp_a.params) | set(exp_b.params)
        for key in all_param_keys:
            if exp_a.params.get(key) != exp_b.params.get(key):
                param_diffs[key] = {
                    "a": exp_a.params.get(key),
                    "b": exp_b.params.get(key),
                }

        score_a = exp_a.metrics.get(primary_metric, 0.0)
        score_b = exp_b.metrics.get(primary_metric, 0.0)
        if score_b > score_a:
            better = exp_b.experiment_id
        elif score_a > score_b:
            better = exp_a.experiment_id
        else:
            better = None

        return ExperimentComparison(
            experiment_a=exp_a,
            experiment_b=exp_b,
            metric_diffs=metric_diffs,
            param_diffs=param_diffs,
            better_experiment=better,
            primary_metric=primary_metric,
        )

    def get_experiment_history(
        self,
        pipeline_id: str,
        *,
        limit: int = 50,
    ) -> list[ExperimentLog]:
        """Get all experiments for a pipeline, newest first."""
        if self._use_mlflow:
            return self._fetch_history_mlflow(pipeline_id, limit=limit)
        return self._fetch_history_local(pipeline_id, limit=limit)

    def search_experiments(
        self,
        *,
        min_metrics: dict[str, float] | None = None,
        tags: dict[str, str] | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[ExperimentLog]:
        """Search experiments by metrics, tags, and date range."""
        all_experiments = self._load_all_local()

        results: list[ExperimentLog] = []
        for exp in all_experiments:
            if min_metrics and not all(
                exp.metrics.get(k, 0.0) >= v for k, v in min_metrics.items()
            ):
                continue
            if tags and not all(exp.tags.get(k) == v for k, v in tags.items()):
                continue
            if after and exp.created_at < after:
                continue
            if before and exp.created_at > before:
                continue
            results.append(exp)

        results.sort(key=lambda e: e.created_at, reverse=True)
        return results[:limit]

    # -- MLflow internals ---------------------------------------------------

    def _log_experiment_mlflow(self, experiment: ExperimentLog) -> None:
        """Log experiment to MLflow."""
        mlflow.set_experiment(experiment.experiment_id)
        run = mlflow.start_run(run_name=experiment.run_name)
        try:
            mlflow.set_tag("pipeline_id", experiment.pipeline_id)
            mlflow.set_tag("evalops_source", "optimization")
            for k, v in experiment.tags.items():
                mlflow.set_tag(k, v)
            for k, v in experiment.params.items():
                mlflow.log_param(k, v)
            for k, v in experiment.metrics.items():
                mlflow.log_metric(k, v)
            experiment.tags["mlflow_run_id"] = run.info.run_id
        finally:
            mlflow.end_run()

    def _log_trial_mlflow(self, experiment_id: str, trial: TrialLog) -> None:
        """Log trial metrics/params to the active MLflow run."""
        mlflow.set_experiment(experiment_id)
        mlflow.start_run(run_name=f"trial-{trial.trial_number}")
        try:
            for k, v in trial.params.items():
                mlflow.log_param(k, v)
            for k, v in trial.metrics.items():
                mlflow.log_metric(k, v)
            mlflow.set_tag("trial_number", str(trial.trial_number))
            for k, v in trial.tags.items():
                mlflow.set_tag(k, v)
        finally:
            mlflow.end_run()

    def _fetch_experiment_mlflow(self, experiment_id: str) -> ExperimentLog | None:
        """Fetch a single experiment from MLflow."""
        if not self._mlflow_client:
            return None
        try:
            mlflow_experiment = self._mlflow_client.get_experiment(experiment_id)
            runs = self._mlflow_client.search_runs(
                experiment_ids=[mlflow_experiment.experiment_id],
                max_results=1,
            )
            if not runs:
                return None
            run = runs[0]
            return ExperimentLog(
                experiment_id=experiment_id,
                pipeline_id=run.data.tags.get("pipeline_id", ""),
                run_name=run.info.run_name or "",
                params=dict(run.data.params),
                metrics=dict(run.data.metrics),
                tags={k: v for k, v in run.data.tags.items() if not k.startswith("mlflow")},
                source="mlflow",
            )
        except Exception:
            logger.exception("experiment_tracker.mlflow_fetch_failed", experiment_id=experiment_id)
            return None

    def _fetch_history_mlflow(
        self, pipeline_id: str, *, limit: int
    ) -> list[ExperimentLog]:
        """Fetch experiment history from MLflow."""
        if not self._mlflow_client:
            return []
        try:
            experiments = self._mlflow_client.search_experiments(
                filter_string=f"tags.pipeline_id = '{pipeline_id}'",
                max_results=limit,
                order_by=["creation_timestamp DESC"],
            )
            results: list[ExperimentLog] = []
            for exp in experiments:
                log = self._fetch_experiment_mlflow(exp.experiment_id)
                if log is not None:
                    results.append(log)
            return results
        except Exception:
            logger.exception("experiment_tracker.mlflow_history_failed", pipeline_id=pipeline_id)
            return []

    # -- Local JSON fallback ------------------------------------------------

    def _experiment_path(self, experiment_id: str) -> Path:
        return self._local_dir / f"{experiment_id}.json"

    def _persist_local(self, experiment: ExperimentLog) -> None:
        """Write experiment to local JSON file."""
        path = self._experiment_path(experiment.experiment_id)
        path.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")

    def _append_trial_local(self, experiment_id: str, trial: TrialLog) -> None:
        """Append a trial to an existing experiment's local JSON file."""
        path = self._experiment_path(experiment_id)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("trials", []).append(trial.model_dump(mode="json"))
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        else:
            exp = ExperimentLog(
                experiment_id=experiment_id,
                pipeline_id="",
                trials=[trial],
            )
            self._persist_local(exp)

    def _get_experiment_local(self, experiment_id: str) -> ExperimentLog | None:
        """Read a single experiment from local JSON."""
        path = self._experiment_path(experiment_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ExperimentLog.model_validate(data)

    def _load_all_local(self) -> list[ExperimentLog]:
        """Load all experiments from local JSON files."""
        if not self._local_dir.exists():
            return []
        experiments: list[ExperimentLog] = []
        for path in self._local_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                experiments.append(ExperimentLog.model_validate(data))
            except Exception:
                logger.warning("experiment_tracker.local_read_failed", path=str(path))
        return experiments

    def _fetch_history_local(
        self, pipeline_id: str, *, limit: int
    ) -> list[ExperimentLog]:
        """Fetch experiment history from local JSON storage."""
        all_exps = self._load_all_local()
        matching = [e for e in all_exps if e.pipeline_id == pipeline_id]
        matching.sort(key=lambda e: e.created_at, reverse=True)
        return matching[:limit]
