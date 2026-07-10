"""Shared fixtures for all EvalOps tests."""

from __future__ import annotations

from typing import Any

import pytest

from backend.core.config import (
    AgentConfig,
    GeneratorConfig,
    GuardrailConfig,
    GuardrailFilterConfig,
    PipelineConfig,
    RerankerConfig,
    RetrievalConfig,
    RetrievalStrategy,
    StepStatus,
)
from backend.core.tracer import TokenUsage, Trajectory, TrajectoryStep, Tracer
from backend.eval.models import (
    EvalResult,
    MetricResult,
    Step,
    StepScore,
    StepType,
    ToolCall,
    Trajectory as EvalTrajectory,
)
from backend.guardrails.filters.base import FilterDecision, FilterResult, RiskLevel


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_pipeline_config() -> PipelineConfig:
    """A default pipeline configuration."""
    return PipelineConfig()


@pytest.fixture
def custom_pipeline_config() -> PipelineConfig:
    """A custom pipeline configuration with non-default values."""
    return PipelineConfig(
        pipeline_id="test-pipeline",
        version="2.0.0",
        retrieval=RetrievalConfig(
            strategy=RetrievalStrategy.DENSE,
            top_k=10,
            similarity_threshold=0.85,
        ),
        reranker=RerankerConfig(top_k=5),
        agent=AgentConfig(
            model="gpt-4o-mini",
            max_tokens=2048,
            temperature=0.3,
        ),
        guardrails=GuardrailConfig(
            enabled=True,
            fail_open=False,
            filters=[
                GuardrailFilterConfig(name="test_filter", threshold=0.7),
            ],
        ),
        generator=GeneratorConfig(
            model="gpt-4o-mini",
            include_citations=False,
        ),
        max_retries=3,
        total_timeout_seconds=300.0,
        enable_tracing=True,
        trace_sample_rate=0.5,
    )


# ---------------------------------------------------------------------------
# Tracer / trajectory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracer() -> Tracer:
    """A Tracer instance with sampling disabled."""
    return Tracer(sample_rate=0.0)


@pytest.fixture
def empty_trajectory() -> Trajectory:
    """An empty trajectory with no steps."""
    return Trajectory(pipeline_id="test-pipeline")


@pytest.fixture
def successful_trajectory() -> Trajectory:
    """A trajectory with all successful steps."""
    traj = Trajectory(pipeline_id="test-pipeline")

    for name in ["retrieve", "rerank", "reason", "guardrail", "generate"]:
        step = TrajectoryStep(
            step_name=name,
            status=StepStatus.SUCCESS,
            tokens=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        step.finish(status=StepStatus.SUCCESS)
        traj.add_step(step)

    traj.finalise()
    return traj


@pytest.fixture
def failing_trajectory() -> Trajectory:
    """A trajectory where the retrieve step fails, cascading to downstream."""
    traj = Trajectory(pipeline_id="test-pipeline")

    # Successful retrieve
    step1 = TrajectoryStep(step_name="retrieve", status=StepStatus.SUCCESS)
    step1.finish(status=StepStatus.SUCCESS)
    traj.add_step(step1)

    # Failing rerank
    step2 = TrajectoryStep(
        step_name="rerank",
        status=StepStatus.FAILED,
        error="Connection timeout",
        error_type="TimeoutError",
    )
    step2.finish(status=StepStatus.FAILED, error="Connection timeout", error_type="TimeoutError")
    traj.add_step(step2)

    # Skipped reason (cascade)
    step3 = TrajectoryStep(step_name="reason", status=StepStatus.SKIPPED)
    step3.finish(status=StepStatus.SKIPPED)
    traj.add_step(step3)

    traj.finalise()
    return traj


# ---------------------------------------------------------------------------
# Eval model fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_step_retrieval() -> Step:
    return Step(
        step_id=0,
        step_type=StepType.RETRIEVAL,
        input_text="search query",
        output_text="",
        context_chunks=["chunk A about cats", "chunk B about dogs"],
        tokens_used=200,
    )


@pytest.fixture
def sample_step_reasoning() -> Step:
    return Step(
        step_id=1,
        step_type=StepType.REASONING,
        input_text="context",
        output_text="Based on the context, the answer is cats.",
        tokens_used=150,
    )


@pytest.fixture
def sample_step_answer() -> Step:
    return Step(
        step_id=2,
        step_type=StepType.ANSWER,
        input_text="reasoning",
        output_text="The answer is cats. Cats are feline animals.",
        tokens_used=100,
    )


@pytest.fixture
def sample_step_tool_call() -> Step:
    return Step(
        step_id=1,
        step_type=StepType.TOOL_CALL,
        tool_calls=[
            ToolCall(
                tool_name="search",
                parameters={"query": "cats"},
                expected_tool="search",
                expected_parameters={"query": "cats"},
            ),
        ],
        tokens_used=50,
    )


@pytest.fixture
def sample_trajectory(
    sample_step_retrieval,
    sample_step_reasoning,
    sample_step_answer,
) -> EvalTrajectory:
    return EvalTrajectory(
        trajectory_id="test-traj-001",
        query="What are cats?",
        steps=[sample_step_retrieval, sample_step_reasoning, sample_step_answer],
        final_answer="The answer is cats. Cats are feline animals.",
        retrieved_context=["chunk A about cats", "chunk B about dogs"],
        total_tokens=450,
        total_cost_usd=0.005,
    )


# ---------------------------------------------------------------------------
# Guardrail fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_filter_result() -> FilterResult:
    return FilterResult(
        filter_name="test",
        decision=FilterDecision.ALLOW,
        score=0.0,
        risk_level=RiskLevel.LOW,
    )


@pytest.fixture
def blocked_filter_result() -> FilterResult:
    return FilterResult(
        filter_name="test",
        decision=FilterDecision.BLOCK,
        score=0.9,
        risk_level=RiskLevel.HIGH,
        blocked_by=["test"],
    )


# ---------------------------------------------------------------------------
# API fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_in_memory_repos():
    """Clear in-memory repository stores between tests for isolation."""
    import backend.api.dependencies as deps

    if hasattr(deps, "_in_memory") and deps._in_memory:
        for repo in deps._in_memory.values():
            repo._store.clear()
    yield
    if hasattr(deps, "_in_memory") and deps._in_memory:
        for repo in deps._in_memory.values():
            repo._store.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"
