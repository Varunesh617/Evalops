"""OpenTelemetry-compatible trajectory tracer for pipeline execution.

Records structured trajectory objects with timestamps, latency, token usage,
and quality scores at each pipeline step.  Designed for async pipelines and
integrates with OpenTelemetry via bridge adapters.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

import structlog

if TYPE_CHECKING:
    from opentelemetry.trace import Span

from backend.core.config import StepStatus

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token consumption for a single step."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:  # noqa: D105
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(slots=True)
class StepMetrics:
    """Quality / performance metrics captured for a single pipeline step."""

    score: float | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TrajectoryStep:
    """A single captured step in a pipeline trajectory."""

    step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    step_name: str = ""
    status: StepStatus = StepStatus.PENDING

    # Timing
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    latency_ms: float | None = None

    # Resources
    tokens: TokenUsage = field(default_factory=TokenUsage)
    metrics: StepMetrics = field(default_factory=StepMetrics)

    # Error tracking
    error: str | None = None
    error_type: str | None = None

    # OpenTelemetry bridge
    span: Span | None = field(default=None, repr=False)

    # Arbitrary payload (retrieved docs, reranked results, generated text, etc.)
    payload: dict[str, Any] = field(default_factory=dict)

    def finish(
        self,
        *,
        status: StepStatus = StepStatus.SUCCESS,
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Mark this step as finished and compute latency."""
        self.end_time = time.monotonic()
        self.latency_ms = (self.end_time - self.start_time) * 1000
        self.status = status
        if error:
            self.error = error
            self.error_type = error_type
        if self.span:
            self.span.set_status(str(status))
            self.span.end()
        logger.info(
            "step_finished",
            step=self.step_name,
            status=str(status),
            latency_ms=round(self.latency_ms, 2),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "status": str(self.status),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "latency_ms": self.latency_ms,
            "tokens": {
                "prompt_tokens": self.tokens.prompt_tokens,
                "completion_tokens": self.tokens.completion_tokens,
                "total_tokens": self.tokens.total_tokens,
            },
            "metrics": {
                "score": self.metrics.score,
                "confidence": self.metrics.confidence,
                "metadata": self.metrics.metadata,
            },
            "error": self.error,
            "error_type": self.error_type,
            "payload_keys": list(self.payload.keys()),
        }


@dataclass(slots=True)
class Trajectory:
    """Full trajectory for a single pipeline run.

    Collects all :class:`TrajectoryStep` instances and provides aggregation
    helpers consumed by the scorer and blame-attribution engine.
    """

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pipeline_id: str = ""
    steps: list[TrajectoryStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None

    # Aggregate fields computed at finalisation
    total_tokens: TokenUsage = field(default_factory=TokenUsage)
    overall_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_step(self, step: TrajectoryStep) -> None:
        """Append a step and accumulate tokens."""
        self.steps.append(step)
        self.total_tokens = self.total_tokens + step.tokens

    def finalise(self) -> None:
        """Mark trajectory as complete and compute aggregates."""
        self.end_time = time.monotonic()

    @property
    def latency_ms(self) -> float | None:
        """Total wall-clock latency in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    @property
    def failed_steps(self) -> list[TrajectoryStep]:
        """Return steps that did not succeed."""
        return [s for s in self.steps if s.status != StepStatus.SUCCESS]

    @property
    def succeeded(self) -> bool:
        """True only when every step succeeded."""
        return len(self.failed_steps) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise trajectory to a JSON-friendly dict."""
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "latency_ms": self.latency_ms,
            "total_tokens": {
                "prompt_tokens": self.total_tokens.prompt_tokens,
                "completion_tokens": self.total_tokens.completion_tokens,
                "total_tokens": self.total_tokens.total_tokens,
            },
            "overall_score": self.overall_score,
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


class Tracer:
    """Async-compatible trajectory tracer.

    Usage::

        tracer = Tracer()
        trajectory = tracer.start(pipeline_id="qa-pipeline")
        async with tracer.step(trajectory, "retrieve") as step:
            docs = await retrieve(query)
            step.payload["documents"] = docs
            step.tokens = TokenUsage(prompt_tokens=120, completion_tokens=0, total_tokens=120)
        # step is auto-finished on context-exit
        trajectory.finalise()
    """

    def __init__(
        self,
        *,
        otel_tracer_name: str = "evalops.pipeline",
        sample_rate: float = 1.0,
    ) -> None:
        self._otel_tracer_name = otel_tracer_name
        self._sample_rate = sample_rate
        self._otel_tracer: Any | None = None
        self._init_otel()

    def _init_otel(self) -> None:
        """Best-effort OpenTelemetry initialisation."""
        try:
            from opentelemetry import trace

            provider = trace.get_tracer_provider()
            self._otel_tracer = provider.get_tracer(self._otel_tracer_name)
        except Exception:
            self._otel_tracer = None

    # -- public API ----------------------------------------------------------

    def start(self, pipeline_id: str = "default") -> Trajectory:
        """Create a fresh trajectory."""
        trajectory = Trajectory(pipeline_id=pipeline_id)
        logger.info("trajectory_started", run_id=trajectory.run_id, pipeline_id=pipeline_id)
        return trajectory

    def _create_step(
        self,
        trajectory: Trajectory,
        step_name: str,
    ) -> TrajectoryStep:
        """Internal helper: create a step with an optional OTel span."""
        span: Span | None = None
        if self._otel_tracer is not None and self._should_sample():
            span = self._otel_tracer.start_span(
                name=f"pipeline.{step_name}",
                attributes={"pipeline.run_id": trajectory.run_id, "step.name": step_name},
            )
        step = TrajectoryStep(step_name=step_name, span=span)
        step.status = StepStatus.RUNNING
        return step

    def _should_sample(self) -> bool:
        import random

        return random.random() < self._sample_rate

    @asynccontextmanager
    async def step(
        self,
        trajectory: Trajectory,
        name: str,
    ) -> AsyncIterator[TrajectoryStep]:
        """Context manager that creates, records, and auto-finishes a step.

        If an exception propagates out of the body the step is marked
        ``FAILED`` with the error details preserved.
        """
        step = self._create_step(trajectory, name)
        trajectory.add_step(step)
        try:
            yield step
            if step.status == StepStatus.RUNNING:
                step.finish(status=StepStatus.SUCCESS)
        except Exception as exc:
            step.finish(
                status=StepStatus.FAILED,
                error=str(exc),
                error_type=type(exc).__qualname__,
            )
            raise

    def finish(self, trajectory: Trajectory) -> Trajectory:
        """Finalise a trajectory and return it."""
        trajectory.finalise()
        logger.info(
            "trajectory_finished",
            run_id=trajectory.run_id,
            total_latency_ms=round(trajectory.latency_ms or 0, 2),
            total_tokens=trajectory.total_tokens.total_tokens,
            succeeded=trajectory.succeeded,
        )
        return trajectory
