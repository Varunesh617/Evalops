"""Fine-tuning trigger from evaluation failures.

Builds training datasets from eval failures, triggers fine-tuning when
a failure threshold is reached, and compares the fine-tuned model against baseline.
"""

from __future__ import annotations

import hashlib
import time
from enum import StrEnum
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TriggerStatus(StrEnum):
    """Status of a fine-tuning trigger evaluation."""

    BELOW_THRESHOLD = "below_threshold"
    THRESHOLD_REACHED = "threshold_reached"
    FT_IN_PROGRESS = "ft_in_progress"
    FT_COMPLETE = "ft_complete"
    FT_FAILED = "ft_failed"
    ROLLBACK = "rollback"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FailureSample(BaseModel):
    """A single failure sample for the FT dataset."""

    sample_id: str
    input_text: str
    expected_output: str
    actual_output: str
    error_type: str
    step_name: str
    trajectory: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    weight: float = Field(default=1.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_training_pair(self) -> dict[str, str]:
        """Convert to a training pair format."""
        return {
            "instruction": self.input_text,
            "input": f"Expected behavior based on trajectory: {self._trajectory_summary()}",
            "output": self.expected_output,
        }

    def _trajectory_summary(self) -> str:
        return " -> ".join(self.trajectory[-3:]) if self.trajectory else "N/A"


class FTDataset(BaseModel):
    """A fine-tuning dataset built from failures."""

    dataset_id: str
    samples: list[FailureSample] = Field(default_factory=list)
    total_samples: int = 0
    avg_weight: float = 0.0
    error_type_distribution: dict[str, int] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class FTJobConfig(BaseModel):
    """Configuration for a fine-tuning job."""

    model_name: str = "gpt-4o-mini"
    base_model: str = "gpt-4o-mini"
    n_epochs: int = 3
    learning_rate: float = 1e-5
    batch_size: int = 8
    max_seq_length: int = 2048
    validation_split: float = 0.1
    early_stopping_patience: int = 2
    lora_rank: int = 16
    use_lora: bool = True


class FTModelComparison(BaseModel):
    """Comparison between baseline and fine-tuned model."""

    baseline_accuracy: float = 0.0
    ft_accuracy: float = 0.0
    accuracy_delta: float = 0.0
    baseline_avg_score: float = 0.0
    ft_avg_score: float = 0.0
    score_delta: float = 0.0
    baseline_cost_usd: float = 0.0
    ft_cost_usd: float = 0.0
    cost_delta: float = 0.0
    improvement_threshold_met: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class FTTriggerResult(BaseModel):
    """Full result of a fine-tuning trigger evaluation."""

    status: TriggerStatus
    failure_count: int = 0
    failure_threshold: int = 0
    threshold_reached: bool = False
    dataset: FTDataset | None = None
    job_config: FTJobConfig | None = None
    job_id: str | None = None
    model_comparison: FTModelComparison | None = None
    recommended_action: str = ""
    total_duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class EvaluatorFn(Protocol):
    """Evaluate a model on a test set and return accuracy + avg score + cost."""

    async def __call__(
        self, model_name: str, test_samples: list[FailureSample]
    ) -> dict[str, Any]: ...


class FTJobRunner(Protocol):
    """Run a fine-tuning job and return a job ID."""

    async def __call__(self, dataset: FTDataset, config: FTJobConfig) -> str: ...


class ModelProvider(Protocol):
    """Provide model names / endpoints."""

    def get_baseline_model(self) -> str: ...

    def get_ft_model(self, job_id: str) -> str: ...


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------


class DatasetBuilder:
    """Build fine-tuning datasets from evaluation failures."""

    def __init__(
        self,
        *,
        min_score_threshold: float = 0.0,
        max_score_threshold: float = 0.5,
        max_samples: int = 10_000,
        deduplicate: bool = True,
    ) -> None:
        self._min_score = min_score_threshold
        self._max_score = max_score_threshold
        self._max_samples = max_samples
        self._deduplicate = deduplicate

    def build(self, failures: list[FailureSample]) -> FTDataset:
        """Filter and build a dataset from failures."""
        # Filter by score range
        filtered = [
            f for f in failures if self._min_score <= f.score <= self._max_score
        ]

        # Deduplicate by input hash
        if self._deduplicate:
            seen: set[str] = set()
            unique: list[FailureSample] = []
            for f in filtered:
                h = hashlib.sha256(f.input_text.encode()).hexdigest()[:16]
                if h not in seen:
                    seen.add(h)
                    unique.append(f)
            filtered = unique

        # Limit size, prioritising lowest scores (most instructive failures)
        filtered.sort(key=lambda f: f.score)
        filtered = filtered[: self._max_samples]

        # Compute weights (inverse score: lower score = higher weight)
        total_weight = 0.0
        for f in filtered:
            f.weight = 1.0 - f.score if f.score < 1.0 else 0.01
            total_weight += f.weight
        avg_weight = total_weight / len(filtered) if filtered else 0.0

        # Error type distribution
        dist: dict[str, int] = {}
        for f in filtered:
            dist[f.error_type] = dist.get(f.error_type, 0) + 1

        dataset_id = hashlib.sha256(
            f"{time.time()}-{len(filtered)}".encode()
        ).hexdigest()[:12]

        return FTDataset(
            dataset_id=dataset_id,
            samples=filtered,
            total_samples=len(filtered),
            avg_weight=avg_weight,
            error_type_distribution=dist,
        )


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------


class FTTrigger:
    """Fine-tuning trigger: builds datasets from failures, triggers FT when
    threshold is reached, and compares models.

    Usage::

        trigger = FTTrigger(
            eval_fn=my_eval,
            ft_runner=my_ft_runner,
            model_provider=my_provider,
        )
        result = await trigger.evaluate(failures)
        if result.status == TriggerStatus.THRESHOLD_REACHED:
            # proceed with FT
    """

    def __init__(
        self,
        eval_fn: EvaluatorFn,
        *,
        ft_runner: FTJobRunner | None = None,
        model_provider: ModelProvider | None = None,
        failure_threshold: int = 50,
        improvement_threshold_pct: float = 2.0,
        min_improvement_score: float = 0.02,
        dataset_builder: DatasetBuilder | None = None,
        job_config: FTJobConfig | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._eval_fn = eval_fn
        self._ft_runner = ft_runner
        self._model_provider = model_provider
        self._failure_threshold = failure_threshold
        self._improvement_threshold_pct = improvement_threshold_pct
        self._min_improvement_score = min_improvement_score
        self._dataset_builder = dataset_builder or DatasetBuilder()
        self._job_config = job_config or FTJobConfig()
        self._metadata = metadata or {}

    async def evaluate(
        self,
        failures: list[FailureSample],
        *,
        existing_job_id: str | None = None,
        test_samples: list[FailureSample] | None = None,
    ) -> FTTriggerResult:
        """Evaluate whether FT should be triggered.

        If *existing_job_id* is provided, compare FT model vs baseline.
        Otherwise, check threshold and build dataset if reached.
        """
        start = time.monotonic()
        log = logger.bind(n_failures=len(failures), threshold=self._failure_threshold)
        log.info("ft_trigger.evaluating")

        # Build dataset from failures
        dataset = self._dataset_builder.build(failures)

        # Check if threshold is reached
        threshold_reached = dataset.total_samples >= self._failure_threshold

        if not threshold_reached:
            log.info(
                "ft_trigger.below_threshold",
                count=dataset.total_samples,
                threshold=self._failure_threshold,
            )
            return FTTriggerResult(
                status=TriggerStatus.BELOW_THRESHOLD,
                failure_count=dataset.total_samples,
                failure_threshold=self._failure_threshold,
                threshold_reached=False,
                dataset=dataset,
                recommended_action=(
                    f"Need {self._failure_threshold - dataset.total_samples} more failures "
                    f"before FT is recommended."
                ),
                total_duration_seconds=time.monotonic() - start,
                metadata=self._metadata,
            )

        # Threshold reached — build dataset and prepare FT job
        log.info("ft_trigger.threshold_reached", count=dataset.total_samples)

        # If we have an existing job, compare models
        if existing_job_id and test_samples:
            comparison = await self._compare_models(existing_job_id, test_samples)
            status = TriggerStatus.FT_COMPLETE
            recommended = self._build_recommendation(comparison)

            if not comparison.improvement_threshold_met:
                status = TriggerStatus.ROLLBACK
                recommended = (
                    f"FT model did not meet improvement threshold "
                    f"(delta={comparison.score_delta:+.4f}). Rollback recommended."
                )

            return FTTriggerResult(
                status=status,
                failure_count=dataset.total_samples,
                failure_threshold=self._failure_threshold,
                threshold_reached=True,
                dataset=dataset,
                job_config=self._job_config,
                job_id=existing_job_id,
                model_comparison=comparison,
                recommended_action=recommended,
                total_duration_seconds=time.monotonic() - start,
                metadata=self._metadata,
            )

        # Trigger new FT job if runner is available
        job_id: str | None = None
        if self._ft_runner is not None:
            try:
                job_id = await self._ft_runner(dataset, self._job_config)
                log.info("ft_trigger.job_started", job_id=job_id)
            except Exception:
                logger.exception("ft_trigger.job_failed")
                return FTTriggerResult(
                    status=TriggerStatus.FT_FAILED,
                    failure_count=dataset.total_samples,
                    failure_threshold=self._failure_threshold,
                    threshold_reached=True,
                    dataset=dataset,
                    job_config=self._job_config,
                    recommended_action="FT job failed to start. Check logs.",
                    total_duration_seconds=time.monotonic() - start,
                    metadata=self._metadata,
                )

        return FTTriggerResult(
            status=TriggerStatus.THRESHOLD_REACHED,
            failure_count=dataset.total_samples,
            failure_threshold=self._failure_threshold,
            threshold_reached=True,
            dataset=dataset,
            job_config=self._job_config,
            job_id=job_id,
            recommended_action=(
                f"Failure threshold reached ({dataset.total_samples}/{self._failure_threshold}). "
                f"FT job {'started' if job_id else 'pending'}. "
                f"Dataset contains {len(dataset.samples)} training samples."
            ),
            total_duration_seconds=time.monotonic() - start,
            metadata=self._metadata,
        )

    async def _compare_models(
        self,
        ft_job_id: str,
        test_samples: list[FailureSample],
    ) -> FTModelComparison:
        """Compare baseline vs fine-tuned model on test samples."""
        baseline_model = (
            self._model_provider.get_baseline_model() if self._model_provider else "baseline"
        )
        ft_model = (
            self._model_provider.get_ft_model(ft_job_id)
            if self._model_provider
            else f"ft-{ft_job_id}"
        )

        baseline_results = await self._eval_fn(baseline_model, test_samples)
        ft_results = await self._eval_fn(ft_model, test_samples)

        baseline_acc = baseline_results.get("accuracy", 0.0)
        ft_acc = ft_results.get("accuracy", 0.0)
        baseline_score = baseline_results.get("avg_score", 0.0)
        ft_score = ft_results.get("avg_score", 0.0)
        baseline_cost = baseline_results.get("cost_usd", 0.0)
        ft_cost = ft_results.get("cost_usd", 0.0)

        acc_delta = ft_acc - baseline_acc
        score_delta = ft_score - baseline_score
        cost_delta = ft_cost - baseline_cost

        # Improvement met if score improves by min threshold AND accuracy improves
        improvement_pct = (score_delta / baseline_score * 100) if baseline_score > 0 else 0.0
        improvement_met = (
            score_delta >= self._min_improvement_score
            and improvement_pct >= self._improvement_threshold_pct
        )

        return FTModelComparison(
            baseline_accuracy=baseline_acc,
            ft_accuracy=ft_acc,
            accuracy_delta=acc_delta,
            baseline_avg_score=baseline_score,
            ft_avg_score=ft_score,
            score_delta=score_delta,
            baseline_cost_usd=baseline_cost,
            ft_cost_usd=ft_cost,
            cost_delta=cost_delta,
            improvement_threshold_met=improvement_met,
            details={
                "improvement_pct": round(improvement_pct, 2),
                "baseline_model": baseline_model,
                "ft_model": ft_model,
                "n_test_samples": len(test_samples),
            },
        )

    def _build_recommendation(self, comparison: FTModelComparison) -> str:
        """Build a human-readable recommendation from model comparison."""
        if comparison.improvement_threshold_met:
            return (
                f"FT model shows improvement: "
                f"accuracy {comparison.accuracy_delta:+.2%}, "
                f"score {comparison.score_delta:+.4f} "
                f"({comparison.score_delta / comparison.baseline_avg_score * 100:+.1f}%). "
                f"Recommended: deploy FT model."
            )
        return (
            f"FT model improvement below threshold: "
            f"accuracy {comparison.accuracy_delta:+.2%}, "
            f"score {comparison.score_delta:+.4f}. "
            f"Recommended: keep baseline model."
        )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


async def quick_ft_check(
    failures: list[FailureSample],
    *,
    eval_fn: EvaluatorFn,
    failure_threshold: int = 50,
    **kwargs: Any,
) -> FTTriggerResult:
    """Quick check whether FT should be triggered."""
    trigger = FTTrigger(eval_fn, failure_threshold=failure_threshold, **kwargs)
    return await trigger.evaluate(failures)
