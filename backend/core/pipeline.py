"""Pipeline executor — orchestrates Retrieve → Rerank → Reason → Guardrail → Generate.

Each step is executed sequentially.  The :class:`Tracer` records a
:mod:`TrajectoryStep` for every stage, making the full execution path
available for downstream scoring and blame attribution.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog

from backend.core.config import PipelineConfig, StepStatus
from backend.core.tracer import TokenUsage, Trajectory, Tracer

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Step protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PipelineStep(Protocol):
    """Any object that can act as a pipeline step."""

    name: str

    async def execute(
        self,
        context: PipelineContext,
    ) -> dict[str, Any]:
        """Execute the step, returning a result dict.

        The result **must** contain at least ``{"status": "success"}`` or
        ``{"status": "failed", "error": "..."}``.
        """
        ...


# ---------------------------------------------------------------------------
# Pipeline context — mutable state shared across steps
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """Shared mutable state carried through pipeline execution.

    Each step reads from and writes to this context.  Downstream steps
    consume outputs produced by upstream steps via well-known keys.
    """

    config: PipelineConfig
    trajectory: Trajectory
    query: str = ""
    results: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Convenience accessors for typed downstream consumption
    @property
    def documents(self) -> list[dict[str, Any]]:
        return self.results.get("retrieve", {}).get("documents", [])  # type: ignore[return-value]

    @property
    def reranked(self) -> list[dict[str, Any]]:
        return self.results.get("rerank", {}).get("documents", [])  # type: ignore[return-value]

    @property
    def reasoning(self) -> str:
        return self.results.get("reason", {}).get("reasoning", "")

    @property
    def guardrail_passed(self) -> bool:
        return self.results.get("guardrail", {}).get("passed", True)

    @property
    def generated(self) -> str:
        return self.results.get("generate", {}).get("text", "")


# ---------------------------------------------------------------------------
# Built-in (skeleton) step implementations
#
# Real implementations will live in backend/steps/*.py and will be injected
# at runtime.  These skeletons validate the executor contract and are useful
# for testing without external services.
# ---------------------------------------------------------------------------


class _BaseStep(abc.ABC):
    """Common base for skeleton steps."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abc.abstractmethod
    async def execute(self, context: PipelineContext) -> dict[str, Any]: ...


class RetrieveStep(_BaseStep):
    """Skeleton retrieval step."""

    def __init__(self) -> None:
        super().__init__("retrieve")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        logger.info("retrieve_started", query=context.query[:80])
        # Real implementation will query vector store.
        return {"status": "success", "documents": [], "count": 0}


class RerankStep(_BaseStep):
    """Skeleton reranker step."""

    def __init__(self) -> None:
        super().__init__("rerank")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        docs = context.documents
        logger.info("rerank_started", input_count=len(docs))
        return {"status": "success", "documents": docs}


class ReasonStep(_BaseStep):
    """Skeleton reasoning / agent step."""

    def __init__(self) -> None:
        super().__init__("reason")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        logger.info("reason_started", doc_count=len(context.reranked))
        return {"status": "success", "reasoning": ""}


class GuardrailStep(_BaseStep):
    """Skeleton guardrail step."""

    def __init__(self) -> None:
        super().__init__("guardrail")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        enabled = context.config.guardrails.enabled
        logger.info("guardrail_started", enabled=enabled)
        if not enabled:
            return {"status": "success", "passed": True, "violations": []}
        return {"status": "success", "passed": True, "violations": []}


class GenerateStep(_BaseStep):
    """Skeleton generation step."""

    def __init__(self) -> None:
        super().__init__("generate")

    async def execute(self, context: PipelineContext) -> dict[str, Any]:
        logger.info("generate_started")
        return {"status": "success", "text": ""}


# ---------------------------------------------------------------------------
# Pipeline executor
# ---------------------------------------------------------------------------


class PipelineExecutor:
    """Orchestrates a sequential pipeline with built-in tracing.

    Parameters
    ----------
    config : PipelineConfig
        Full pipeline configuration.
    steps : list[PipelineStep] | None
        Ordered list of steps to execute.  When *None*, the default
        sequence ``Retrieve → Rerank → Reason → Guardrail → Generate``
        is used.
    tracer : Tracer | None
        Optional external tracer.  One is created automatically when omitted.
    """

    @classmethod
    def _default_steps(cls) -> list[PipelineStep]:
        return [
            RetrieveStep(),
            RerankStep(),
            ReasonStep(),
            GuardrailStep(),
            GenerateStep(),
        ]

    def __init__(
        self,
        config: PipelineConfig,
        steps: list[PipelineStep] | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.config = config
        self.steps = steps if steps is not None else self._default_steps()
        self.tracer = tracer or Tracer(
            sample_rate=config.trace_sample_rate,
        )

    async def execute(self, query: str) -> Trajectory:
        """Run the full pipeline for *query* and return its trajectory.

        Steps are executed sequentially.  If a step fails, execution halts
        and the trajectory is finalised with the failure recorded.
        """
        trajectory = self.tracer.start(pipeline_id=self.config.pipeline_id)
        ctx = PipelineContext(config=self.config, trajectory=trajectory, query=query)
        try:
            for pipeline_step in self.steps:
                async with self.tracer.step(trajectory, pipeline_step.name) as step:
                    try:
                        result = await pipeline_step.execute(ctx)
                    except Exception as exc:
                        result = {
                            "status": "failed",
                            "error": str(exc),
                            "error_type": type(exc).__qualname__,
                        }
                        step.payload["result"] = result
                        raise

                    status = result.pop("status", "success")
                    step.payload["result"] = result

                    # Record tokens if the step provided them
                    if "tokens" in result:
                        tok = result.pop("tokens")
                        step.tokens = TokenUsage(**tok)

                    if status == "failed":
                        step.finish(
                            status=StepStatus.FAILED,
                            error=result.get("error"),
                            error_type=result.get("error_type"),
                        )
                        ctx.results[pipeline_step.name] = result
                        logger.error(
                            "step_failed",
                            step=pipeline_step.name,
                            error=result.get("error"),
                        )
                        break  # halt on failure

                    ctx.results[pipeline_step.name] = result
        finally:
            self.tracer.finish(trajectory)
        return trajectory


# ---------------------------------------------------------------------------
# Pipeline builder (fluent API)
# ---------------------------------------------------------------------------


class PipelineBuilder:
    """Fluent builder for constructing a custom pipeline."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self._config = config or PipelineConfig()
        self._steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> PipelineBuilder:
        """Append a step to the pipeline."""
        self._steps.append(step)
        return self

    def with_config(self, config: PipelineConfig) -> PipelineBuilder:
        """Replace the configuration."""
        self._config = config
        return self

    def build(self) -> PipelineExecutor:
        """Build the executor.  Uses default steps when none were added."""
        steps = self._steps if self._steps else None
        return PipelineExecutor(config=self._config, steps=steps)
