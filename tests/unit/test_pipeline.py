"""Tests for the pipeline executor in backend.core.pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from backend.core.config import PipelineConfig, StepStatus
from backend.core.pipeline import (
    GenerateStep,
    GuardrailStep,
    PipelineBuilder,
    PipelineContext,
    PipelineExecutor,
    ReasonStep,
    RetrieveStep,
    RerankStep,
)
from backend.core.tracer import Trajectory, Tracer


# ---------------------------------------------------------------------------
# PipelineContext tests
# ---------------------------------------------------------------------------


class TestPipelineContext:
    def test_documents_property_empty(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        assert ctx.documents == []

    def test_documents_property_with_data(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        ctx.results["retrieve"] = {"documents": [{"id": 1}, {"id": 2}]}
        assert len(ctx.documents) == 2

    def test_reranked_property(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        ctx.results["rerank"] = {"documents": [{"id": 1}]}
        assert len(ctx.reranked) == 1

    def test_reasoning_property(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        ctx.results["reason"] = {"reasoning": "The answer is 42."}
        assert ctx.reasoning == "The answer is 42."

    def test_guardrail_passed_default(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        assert ctx.guardrail_passed is True

    def test_guardrail_passed_blocked(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        ctx.results["guardrail"] = {"passed": False}
        assert ctx.guardrail_passed is False

    def test_generated_property(self):
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        ctx.results["generate"] = {"text": "Hello world"}
        assert ctx.generated == "Hello world"


# ---------------------------------------------------------------------------
# Skeleton step tests
# ---------------------------------------------------------------------------


class TestRetrieveStep:
    @pytest.mark.asyncio
    async def test_execute(self):
        step = RetrieveStep()
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory(), query="test query")
        result = await step.execute(ctx)
        assert result["status"] == "success"
        assert result["documents"] == []
        assert result["count"] == 0

    def test_name(self):
        assert RetrieveStep().name == "retrieve"


class TestRerankStep:
    @pytest.mark.asyncio
    async def test_execute(self):
        step = RerankStep()
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        ctx.results["retrieve"] = {"documents": [{"id": 1}]}
        result = await step.execute(ctx)
        assert result["status"] == "success"
        assert len(result["documents"]) == 1

    def test_name(self):
        assert RerankStep().name == "rerank"


class TestReasonStep:
    @pytest.mark.asyncio
    async def test_execute(self):
        step = ReasonStep()
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        result = await step.execute(ctx)
        assert result["status"] == "success"
        assert "reasoning" in result

    def test_name(self):
        assert ReasonStep().name == "reason"


class TestGuardrailStep:
    @pytest.mark.asyncio
    async def test_execute_enabled(self):
        step = GuardrailStep()
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        result = await step.execute(ctx)
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_execute_disabled(self):
        step = GuardrailStep()
        config = PipelineConfig(guardrails={"enabled": False})
        ctx = PipelineContext(config=config, trajectory=Trajectory())
        result = await step.execute(ctx)
        assert result["passed"] is True

    def test_name(self):
        assert GuardrailStep().name == "guardrail"


class TestGenerateStep:
    @pytest.mark.asyncio
    async def test_execute(self):
        step = GenerateStep()
        ctx = PipelineContext(config=PipelineConfig(), trajectory=Trajectory())
        result = await step.execute(ctx)
        assert result["status"] == "success"

    def test_name(self):
        assert GenerateStep().name == "generate"


# ---------------------------------------------------------------------------
# PipelineExecutor tests
# ---------------------------------------------------------------------------


class TestPipelineExecutor:
    def test_default_steps(self):
        executor = PipelineExecutor(config=PipelineConfig())
        assert len(executor.steps) == 5
        names = [s.name for s in executor.steps]
        assert names == ["retrieve", "rerank", "reason", "guardrail", "generate"]

    def test_custom_steps(self):
        custom = [RetrieveStep(), GenerateStep()]
        executor = PipelineExecutor(config=PipelineConfig(), steps=custom)
        assert len(executor.steps) == 2

    def test_custom_tracer(self):
        tracer = Tracer(sample_rate=0.0)
        executor = PipelineExecutor(config=PipelineConfig(), tracer=tracer)
        assert executor.tracer is tracer

    @pytest.mark.asyncio
    async def test_execute_full_pipeline(self):
        executor = PipelineExecutor(config=PipelineConfig())
        trajectory = await executor.execute("What is 2+2?")
        assert trajectory.pipeline_id == "default"
        assert len(trajectory.steps) == 5
        assert trajectory.succeeded is True
        assert trajectory.end_time is not None

    @pytest.mark.asyncio
    async def test_execute_with_token_tracking(self):
        """Test that token usage from step results gets recorded."""

        class TokenStep:
            name = "token_step"

            async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
                return {
                    "status": "success",
                    "tokens": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "data": "result",
                }

        executor = PipelineExecutor(config=PipelineConfig(), steps=[TokenStep()])
        traj = await executor.execute("test")
        assert traj.total_tokens.total_tokens == 150

    @pytest.mark.asyncio
    async def test_execute_step_failure_halts(self):
        class FailStep:
            name = "fail_step"

            async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
                return {"status": "failed", "error": "something broke"}

        class AfterFailStep:
            name = "after_fail"

            async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
                raise AssertionError("should not run")

        executor = PipelineExecutor(
            config=PipelineConfig(), steps=[FailStep(), AfterFailStep()]
        )
        traj = await executor.execute("test")
        assert len(traj.steps) == 1  # Only the first step ran + was auto-finished by tracer
        assert traj.failed_steps

    @pytest.mark.asyncio
    async def test_execute_exception_propagates(self):
        class RaiseStep:
            name = "raise_step"

            async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
                raise RuntimeError("kaboom")

        executor = PipelineExecutor(config=PipelineConfig(), steps=[RaiseStep()])
        with pytest.raises(RuntimeError, match="kaboom"):
            await executor.execute("test")

    @pytest.mark.asyncio
    async def test_execute_custom_pipeline_id(self):
        config = PipelineConfig(pipeline_id="my-pipeline")
        executor = PipelineExecutor(config=config)
        traj = await executor.execute("test")
        assert traj.pipeline_id == "my-pipeline"


# ---------------------------------------------------------------------------
# PipelineBuilder tests
# ---------------------------------------------------------------------------


class TestPipelineBuilder:
    def test_build_default(self):
        builder = PipelineBuilder()
        executor = builder.build()
        assert isinstance(executor, PipelineExecutor)
        assert len(executor.steps) == 5

    def test_add_step(self):
        step = RetrieveStep()
        builder = PipelineBuilder()
        builder.add_step(step)
        executor = builder.build()
        assert len(executor.steps) == 1
        assert executor.steps[0].name == "retrieve"

    def test_chaining(self):
        executor = (
            PipelineBuilder()
            .add_step(RetrieveStep())
            .add_step(RerankStep())
            .build()
        )
        assert len(executor.steps) == 2

    def test_with_config(self):
        config = PipelineConfig(pipeline_id="custom")
        executor = PipelineBuilder().with_config(config).build()
        assert executor.config.pipeline_id == "custom"

    @pytest.mark.asyncio
    async def test_build_and_execute(self):
        executor = PipelineBuilder().add_step(RetrieveStep()).build()
        traj = await executor.execute("test")
        assert traj.succeeded is True
