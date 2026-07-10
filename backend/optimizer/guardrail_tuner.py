"""Guardrail threshold tuner using Bayesian optimization.

Jointly optimises guardrail filter thresholds to minimise false positive rate
while maximising true positive rate across all filters.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import optuna
import structlog
from pydantic import BaseModel, Field

from backend.core.config import (
    GuardrailConfig,
    GuardrailFilterConfig,
    GuardrailSeverity,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GuardrailEvalOutcome(BaseModel):
    """Result of evaluating a guardrail configuration against labeled data."""

    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    true_negatives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    total_samples: int = Field(ge=0)

    @property
    def tpr(self) -> float:
        """True positive rate (recall / sensitivity)."""
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def fpr(self) -> float:
        """False positive rate."""
        denom = self.false_positives + self.true_negatives
        return self.false_positives / denom if denom > 0 else 0.0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.tpr
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        denom = self.true_positives + self.true_negatives
        return denom / self.total_samples if self.total_samples > 0 else 0.0


class TunedFilter(BaseModel):
    """A single filter with its optimised threshold."""

    name: str
    original_threshold: float
    optimised_threshold: float
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.MEDIUM


class TunerResult(BaseModel):
    """Full result of a guardrail tuning run."""

    optimised_config: GuardrailConfig
    tuned_filters: list[TunedFilter] = Field(default_factory=list)
    baseline_outcome: GuardrailEvalOutcome
    optimised_outcome: GuardrailEvalOutcome
    improvement_fpr: float = 0.0
    improvement_tpr: float = 0.0
    improvement_f1: float = 0.0
    trials_completed: int = 0
    total_duration_seconds: float = 0.0
    param_importances: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class GuardrailEvalFn(Protocol):
    """Protocol: evaluate a guardrail config against labeled data."""

    async def __call__(self, guardrail_config: GuardrailConfig) -> GuardrailEvalOutcome: ...


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------


def _build_search_space(
    trial: optuna.Trial,
    filter_configs: list[GuardrailFilterConfig],
) -> dict[str, float]:
    """Suggest thresholds for each filter; returns dict[name -> threshold]."""
    thresholds: dict[str, float] = {}
    for fc in filter_configs:
        if fc.enabled:
            thresholds[fc.name] = trial.suggest_float(
                f"threshold_{fc.name}",
                0.1,
                0.99,
                step=0.01,
            )
    return thresholds


def _apply_thresholds(
    base_config: GuardrailConfig,
    thresholds: dict[str, float],
) -> GuardrailConfig:
    """Return a new GuardrailConfig with overridden thresholds."""
    updated_filters = []
    for fc in base_config.filters:
        new_threshold = thresholds.get(fc.name, fc.threshold)
        updated_filters.append(fc.model_copy(update={"threshold": new_threshold}))
    return base_config.model_copy(update={"filters": updated_filters})


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------


def _composite_objective(outcome: GuardrailEvalOutcome, fpr_penalty: float = 0.4) -> float:
    """Higher is better.  Balances TPR and inverse-FPR.

    objective = TPR - fpr_penalty * FPR + 0.1 * F1
    """
    return outcome.tpr - fpr_penalty * outcome.fpr + 0.1 * outcome.f1


# ---------------------------------------------------------------------------
# Tuner
# ---------------------------------------------------------------------------


class GuardrailTuner:
    """Bayesian-optimisation tuner for guardrail filter thresholds.

    Usage::

        tuner = GuardrailTuner(
            eval_fn=my_guardrail_eval,
            base_config=guardrail_config,
        )
        result = await tuner.run()
    """

    def __init__(
        self,
        eval_fn: GuardrailEvalFn,
        *,
        base_config: GuardrailConfig,
        n_trials: int = 60,
        fpr_penalty: float = 0.4,
        study_name: str | None = None,
        storage: str | None = None,
        sampler: optuna.samplers.BaseSampler | None = None,
        pruner: optuna.pruners.BasePruner | None = None,
    ) -> None:
        self._eval_fn = eval_fn
        self._base_config = base_config
        self._n_trials = n_trials
        self._fpr_penalty = fpr_penalty
        self._study_name = study_name or "evalops-guardrail-tune"
        self._storage = storage
        self._sampler = sampler or optuna.samplers.TPESampler(seed=42)
        self._pruner = pruner or optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=3)

    async def run(self) -> TunerResult:
        """Run the tuning loop and return the best guardrail configuration."""
        study = optuna.create_study(
            study_name=self._study_name,
            storage=self._storage,
            direction="maximize",
            sampler=self._sampler,
            pruner=self._pruner,
            load_if_exists=True,
        )

        filter_configs = [fc for fc in self._base_config.filters if fc.enabled]
        if not filter_configs:
            logger.warning("guardrail_tuner.no_filters_enabled")
            baseline = await self._eval_fn(self._base_config)
            return TunerResult(
                optimised_config=self._base_config,
                tuned_filters=[],
                baseline_outcome=baseline,
                optimised_outcome=baseline,
                trials_completed=0,
            )

        start = time.monotonic()
        log = logger.bind(study=self._study_name, n_filters=len(filter_configs))
        log.info("guardrail_tuner.started")

        baseline_outcome = await self._eval_fn(self._base_config)
        best_outcome = baseline_outcome

        for trial_num in range(self._n_trials):
            trial = study.ask()
            thresholds = _build_search_space(trial, filter_configs)
            trial_config = _apply_thresholds(self._base_config, thresholds)

            try:
                import anyio

                outcome = await anyio.to_thread.run_sync(
                    lambda tc=trial_config: _run_guardrail_eval_sync(
                        self._eval_fn, tc
                    ),
                )
            except Exception:
                logger.exception("guardrail_tuner.trial_failed", trial=trial_num)
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                continue

            score = _composite_objective(outcome, self._fpr_penalty)
            study.tell(trial, score)

            if score > _composite_objective(best_outcome, self._fpr_penalty):
                best_outcome = outcome

            log.info(
                "guardrail_tuner.trial_completed",
                trial=trial_num,
                tpr=round(outcome.tpr, 4),
                fpr=round(outcome.fpr, 4),
                f1=round(outcome.f1, 4),
                score=round(score, 4),
            )

        total_duration = time.monotonic() - start
        best_trial = study.best_trial
        best_thresholds = _build_search_space(best_trial, filter_configs)
        optimised_config = _apply_thresholds(self._base_config, best_thresholds)

        tuned_filters: list[TunedFilter] = []
        for fc in filter_configs:
            orig_t = fc.threshold
            new_t = best_thresholds.get(fc.name, orig_t)
            tuned_filters.append(
                TunedFilter(
                    name=fc.name,
                    original_threshold=orig_t,
                    optimised_threshold=new_t,
                    enabled=fc.enabled,
                    severity=fc.severity,
                )
            )

        param_importances = optuna.importance.get_param_importances(study)

        log.info(
            "guardrail_tuner.completed",
            trials_completed=len(study.trials),
            improvement_tpr=round(best_outcome.tpr - baseline_outcome.tpr, 4),
            improvement_fpr=round(baseline_outcome.fpr - best_outcome.fpr, 4),
            total_seconds=round(total_duration, 2),
        )

        return TunerResult(
            optimised_config=optimised_config,
            tuned_filters=tuned_filters,
            baseline_outcome=baseline_outcome,
            optimised_outcome=best_outcome,
            improvement_fpr=baseline_outcome.fpr - best_outcome.fpr,
            improvement_tpr=best_outcome.tpr - baseline_outcome.tpr,
            improvement_f1=best_outcome.f1 - baseline_outcome.f1,
            trials_completed=len(study.trials),
            total_duration_seconds=total_duration,
            param_importances=param_importances,
        )


def _run_guardrail_eval_sync(
    eval_fn: GuardrailEvalFn, config: GuardrailConfig
) -> GuardrailEvalOutcome:
    """Bridge async eval to sync for thread pool."""
    import anyio

    async def _inner() -> GuardrailEvalOutcome:
        return await eval_fn(config)

    return anyio.from_thread.run(_inner)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


async def quick_tune_guardrails(
    eval_fn: GuardrailEvalFn,
    *,
    base_config: GuardrailConfig,
    n_trials: int = 30,
    **kwargs: Any,
) -> TunerResult:
    """Run a quick guardrail tuning sweep."""
    tuner = GuardrailTuner(eval_fn, base_config=base_config, n_trials=n_trials, **kwargs)
    return await tuner.run()
