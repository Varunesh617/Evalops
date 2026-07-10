"""Optuna-based pipeline configuration sweeper.

Runs N trials of pipeline evaluation, tracking cost + quality per trial,
and returns the best configuration found via Bayesian optimization.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import optuna
import structlog
from pydantic import BaseModel, Field

from backend.core.config import (
    AgentConfig,
    GeneratorConfig,
    GuardrailConfig,
    GuardrailFilterConfig,
    PipelineConfig,
    RerankerConfig,
    RerankerModel,
    RetrievalConfig,
    RetrievalStrategy,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class TrialResult(BaseModel):
    """Result of a single optimization trial."""

    trial_number: int
    params: dict[str, Any]
    quality_score: float = Field(ge=0.0, le=1.0)
    cost_usd: float = Field(ge=0.0)
    latency_ms: float = Field(ge=0.0)
    composite_score: float
    duration_seconds: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SweepResult(BaseModel):
    """Aggregated result of a full sweep."""

    best_config: PipelineConfig
    best_composite_score: float
    best_quality_score: float
    best_cost_usd: float
    best_latency_ms: float
    trials_completed: int
    trials_pruned: int
    total_duration_seconds: float
    all_trials: list[TrialResult] = Field(default_factory=list)
    param_importances: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class EvalFunction(Protocol):
    """Protocol for the evaluation function passed to the sweeper."""

    async def __call__(self, config: PipelineConfig) -> EvalOutcome: ...


@dataclass
class EvalOutcome:
    """Raw output from a single pipeline evaluation run."""

    quality_score: float
    cost_usd: float
    latency_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Search space definition
# ---------------------------------------------------------------------------


def define_search_space(trial: optuna.Trial) -> PipelineConfig:
    """Suggest a full PipelineConfig from the Optuna trial search space.

    Covers retrieval, reranker, agent, guardrail, and generator knobs.
    """
    retrieval_strategy = trial.suggest_categorical(
        "retrieval_strategy",
        [s.value for s in RetrievalStrategy],
    )
    retrieval_top_k = trial.suggest_int("retrieval_top_k", 5, 100, step=5)
    retrieval_sim_threshold = trial.suggest_float("retrieval_sim_threshold", 0.3, 1.0, step=0.05)
    retrieval_embedding_model = trial.suggest_categorical(
        "retrieval_embedding_model",
        ["text-embedding-3-small", "text-embedding-3-large", "voyage-3"],
    )
    retrieval_embedding_dim = trial.suggest_categorical(
        "retrieval_embedding_dim",
        [1536, 3072, 1024],
    )

    reranker_model = trial.suggest_categorical(
        "reranker_model",
        [m.value for m in RerankerModel],
    )
    reranker_top_k = trial.suggest_int("reranker_top_k", 3, 50, step=1)
    reranker_batch_size = trial.suggest_int("reranker_batch_size", 8, 128, step=8)

    agent_model = trial.suggest_categorical(
        "agent_model",
        ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-5-haiku"],
    )
    agent_max_tokens = trial.suggest_int("agent_max_tokens", 512, 8192, step=256)
    agent_temperature = trial.suggest_float("agent_temperature", 0.0, 1.5, step=0.05)
    agent_max_tool_calls = trial.suggest_int("agent_max_tool_calls", 0, 20, step=1)

    guardrail_enabled = trial.suggest_categorical("guardrail_enabled", [True, False])
    guardrail_fail_open = trial.suggest_categorical("guardrail_fail_open", [True, False])

    generator_model = trial.suggest_categorical(
        "generator_model",
        ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-5-haiku"],
    )
    generator_max_tokens = trial.suggest_int("generator_max_tokens", 512, 8192, step=256)
    generator_temperature = trial.suggest_float("generator_temperature", 0.0, 1.0, step=0.05)
    generator_include_citations = trial.suggest_categorical(
        "generator_include_citations", [True, False]
    )

    filters = _suggest_guardrail_filters(trial) if guardrail_enabled else []

    return PipelineConfig(
        retrieval=RetrievalConfig(
            strategy=RetrievalStrategy(retrieval_strategy),
            top_k=retrieval_top_k,
            embedding_model=retrieval_embedding_model,
            embedding_dim=retrieval_embedding_dim,
            similarity_threshold=retrieval_sim_threshold,
        ),
        reranker=RerankerConfig(
            model=RerankerModel(reranker_model),
            top_k=reranker_top_k,
            batch_size=reranker_batch_size,
        ),
        agent=AgentConfig(
            model=agent_model,
            max_tokens=agent_max_tokens,
            temperature=agent_temperature,
            max_tool_calls=agent_max_tool_calls,
        ),
        guardrails=GuardrailConfig(
            enabled=guardrail_enabled,
            fail_open=guardrail_fail_open,
            filters=filters,
        ),
        generator=GeneratorConfig(
            model=generator_model,
            max_tokens=generator_max_tokens,
            temperature=generator_temperature,
            include_citations=generator_include_citations,
        ),
    )


def _suggest_guardrail_filters(trial: optuna.Trial) -> list[GuardrailFilterConfig]:
    """Suggest guardrail filter configurations."""
    filter_names = [
        "prompt_injection", "pii", "toxicity",
        "faithfulness_check", "citation_validator",
    ]
    filters: list[GuardrailFilterConfig] = []
    for name in filter_names:
        enabled = trial.suggest_categorical(f"filter_{name}_enabled", [True, False])
        if enabled:
            threshold = trial.suggest_float(f"filter_{name}_threshold", 0.3, 0.99, step=0.01)
            filters.append(GuardrailFilterConfig(name=name, enabled=True, threshold=threshold))
    return filters


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def compute_composite_score(
    *,
    quality: float,
    cost_usd: float,
    latency_ms: float,
    quality_weight: float = 0.6,
    cost_weight: float = 0.25,
    latency_weight: float = 0.15,
    max_cost_usd: float = 5.0,
    max_latency_ms: float = 30_000.0,
) -> float:
    """Weighted composite score.  Higher is better.

    Normalises cost and latency into 0-1 range (inverted) and blends with quality.
    """
    norm_cost = max(0.0, 1.0 - cost_usd / max_cost_usd)
    norm_latency = max(0.0, 1.0 - latency_ms / max_latency_ms)
    return quality_weight * quality + cost_weight * norm_cost + latency_weight * norm_latency


# ---------------------------------------------------------------------------
# Main sweeper
# ---------------------------------------------------------------------------


class ConfigSweeper:
    """Optuna-based pipeline configuration sweeper.

    Usage::

        sweeper = ConfigSweeper(eval_fn=my_eval, n_trials=50)
        result = await sweeper.run()
        print(result.best_config, result.best_composite_score)
    """

    def __init__(
        self,
        eval_fn: EvalFunction,
        *,
        n_trials: int = 50,
        timeout_seconds: float | None = None,
        quality_weight: float = 0.6,
        cost_weight: float = 0.25,
        latency_weight: float = 0.15,
        max_cost_usd: float = 5.0,
        max_latency_ms: float = 30_000.0,
        study_name: str | None = None,
        storage: str | None = None,
        direction: str = "maximize",
        sampler: optuna.samplers.BaseSampler | None = None,
        pruner: optuna.pruners.BasePruner | None = None,
    ) -> None:
        self._eval_fn = eval_fn
        self._n_trials = n_trials
        self._timeout_seconds = timeout_seconds
        self._quality_weight = quality_weight
        self._cost_weight = cost_weight
        self._latency_weight = latency_weight
        self._max_cost_usd = max_cost_usd
        self._max_latency_ms = max_latency_ms
        self._study_name = study_name or "evalops-sweep"
        self._storage = storage
        self._direction = direction
        self._sampler = sampler or optuna.samplers.TPESampler(seed=42)
        self._pruner = pruner or optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=3)

    def _create_study(self) -> optuna.Study:
        return optuna.create_study(
            study_name=self._study_name,
            storage=self._storage,
            direction=self._direction,
            sampler=self._sampler,
            pruner=self._pruner,
            load_if_exists=True,
        )

    async def run(self) -> SweepResult:
        """Execute the sweep and return aggregated results."""
        study = self._create_study()
        all_trials: list[TrialResult] = []
        start = time.monotonic()

        log = logger.bind(study=self._study_name, n_trials=self._n_trials)
        log.info("config_sweep.started")

        for trial_num in range(self._n_trials):
            trial = study.ask()
            config = define_search_space(trial)

            trial_start = time.monotonic()
            try:
                import anyio

                outcome = await anyio.to_thread.run_sync(
                    lambda c=config: _run_eval_sync(self._eval_fn, c),  # type: ignore[arg-type]
                )
            except Exception:
                logger.exception("config_sweep.trial_failed", trial=trial_num)
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                continue

            composite = compute_composite_score(
                quality=outcome.quality_score,
                cost_usd=outcome.cost_usd,
                latency_ms=outcome.latency_ms,
                quality_weight=self._quality_weight,
                cost_weight=self._cost_weight,
                latency_weight=self._latency_weight,
                max_cost_usd=self._max_cost_usd,
                max_latency_ms=self._max_latency_ms,
            )
            study.tell(trial, composite)

            duration = time.monotonic() - trial_start
            result = TrialResult(
                trial_number=trial_num,
                params=trial.params,
                quality_score=outcome.quality_score,
                cost_usd=outcome.cost_usd,
                latency_ms=outcome.latency_ms,
                composite_score=composite,
                duration_seconds=duration,
                metadata=outcome.metadata,
            )
            all_trials.append(result)

            log.info(
                "config_sweep.trial_completed",
                trial=trial_num,
                composite=round(composite, 4),
                quality=round(outcome.quality_score, 4),
                cost=round(outcome.cost_usd, 4),
            )

        total_duration = time.monotonic() - start
        best = study.best_trial
        best_config = define_search_space(best)

        param_importances = optuna.importance.get_param_importances(study)

        completed = len(all_trials)
        pruned = sum(1 for t in all_trials if False)  # placeholder; pruner counts via study

        log.info(
            "config_sweep.completed",
            completed=completed,
            best_score=round(best.value, 4) if best.value else 0,
            total_seconds=round(total_duration, 2),
        )

        return SweepResult(
            best_config=best_config,
            best_composite_score=best.value if best.value is not None else 0.0,
            best_quality_score=best.user_attrs.get("quality_score", 0.0),
            best_cost_usd=best.user_attrs.get("cost_usd", 0.0),
            best_latency_ms=best.user_attrs.get("latency_ms", 0.0),
            trials_completed=completed,
            trials_pruned=pruned,
            total_duration_seconds=total_duration,
            all_trials=all_trials,
            param_importances=param_importances,
        )


def _run_eval_sync(eval_fn: EvalFunction, config: PipelineConfig) -> EvalOutcome:
    """Bridge async eval_fn to sync context for thread pool execution."""
    import anyio

    async def _inner() -> EvalOutcome:
        return await eval_fn(config)

    return anyio.from_thread.run(_inner)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


async def quick_sweep(
    eval_fn: EvalFunction,
    *,
    n_trials: int = 20,
    **kwargs: Any,
) -> SweepResult:
    """Run a quick sweep with sensible defaults."""
    sweeper = ConfigSweeper(eval_fn, n_trials=n_trials, **kwargs)
    return await sweeper.run()
