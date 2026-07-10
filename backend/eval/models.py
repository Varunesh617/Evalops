"""Pydantic models for the evaluation engine."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field


class StepType(str, enum.Enum):
    """Types of steps in a trajectory."""

    QUERY = "query"
    RETRIEVAL = "retrieval"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    REASONING = "reasoning"
    ANSWER = "answer"
    GUARDRAIL_CHECK = "guardrail_check"
    GUARDRAIL_BLOCK = "guardrail_block"


class ToolCall(BaseModel):
    """A tool invocation within a trajectory step."""

    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_tool: str | None = None
    expected_parameters: dict[str, Any] | None = None


class Step(BaseModel):
    """A single step in an agent trajectory."""

    step_id: int
    step_type: StepType
    input_text: str = ""
    output_text: str = ""
    context_chunks: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Trajectory(BaseModel):
    """A complete agent execution trajectory to be evaluated."""

    trajectory_id: str
    query: str
    steps: list[Step] = Field(default_factory=list)
    final_answer: str = ""
    retrieved_context: list[str] = Field(default_factory=list)
    guardrail_blocked: bool = False
    guardrail_is_legitimate: bool = True
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepScore(BaseModel):
    """Score for a single step evaluated against a single metric."""

    step_id: int
    metric_name: str
    score: float = Field(ge=0.0, le=1.0)
    details: str = ""
    breakdown: dict[str, Any] = Field(default_factory=dict)


class MetricResult(BaseModel):
    """Aggregate result from running one metric across a trajectory."""

    metric_name: str
    overall_score: float = Field(ge=0.0, le=1.0)
    step_scores: list[StepScore] = Field(default_factory=list)
    details: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalResult(BaseModel):
    """Full result from an eval run across all requested metrics."""

    trajectory_id: str
    metric_results: list[MetricResult] = Field(default_factory=list)

    @property
    def scores(self) -> dict[str, float]:
        """Return metric name -> overall score mapping."""
        return {mr.metric_name: mr.overall_score for mr in self.metric_results}

    @property
    def aggregate_score(self) -> float:
        """Weighted average of all metric scores."""
        if not self.metric_results:
            return 0.0
        return sum(mr.overall_score for mr in self.metric_results) / len(
            self.metric_results
        )
