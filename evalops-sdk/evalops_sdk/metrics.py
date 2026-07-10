"""Metric authoring helpers for EvalOps plugin authors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field


class MetricSpec(BaseModel):
    """Declarative specification for a metric plugin."""

    plugin_id: str
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    requires_context: bool = False


@dataclass
class StepScorer:
    """Wrapper that turns a simple function into a step scorer."""

    fn: Callable[[str, str], float]
    description: str = ""

    def score(self, input_text: str, output_text: str) -> float:
        result = self.fn(input_text, output_text)
        return max(0.0, min(1.0, result))


class MetricAuthoring:
    """Helper class for building metric plugins with a fluent API."""

    def __init__(self, spec: MetricSpec) -> None:
        self._spec = spec
        self._scorers: list[StepScorer] = []
        self._aggregator: Callable[[list[float]], float] | None = None
        self._config: dict[str, Any] = {}

    @classmethod
    def define(cls, plugin_id: str, name: str) -> MetricAuthoring:
        """Start defining a new metric."""
        return cls(MetricSpec(plugin_id=plugin_id, name=name))

    def version(self, version: str) -> MetricAuthoring:
        self._spec.version = version
        return self

    def author(self, author: str) -> MetricAuthoring:
        self._spec.author = author
        return self

    def description(self, description: str) -> MetricAuthoring:
        self._spec.description = description
        return self

    def tags(self, *tags: str) -> MetricAuthoring:
        self._spec.tags = list(tags)
        return self

    def scorer(
        self, fn: Callable[[str, str], float], *, description: str = ""
    ) -> MetricAuthoring:
        """Add a scoring function that receives (input, output) → float [0,1]."""
        self._scorers.append(StepScorer(fn=fn, description=description))
        return self

    def aggregate(
        self, fn: Callable[[list[float]], float]
    ) -> MetricAuthoring:
        """Set a custom aggregation function (list of scores → single score)."""
        self._aggregator = fn
        return self

    def config(self, **kwargs: Any) -> MetricAuthoring:
        """Set default configuration values."""
        self._config.update(kwargs)
        return self

    def build(self) -> dict[str, Any]:
        """Return the final metric specification."""
        return {
            "spec": self._spec.model_dump(),
            "scorers": [
                {"description": s.description} for s in self._scorers
            ],
            "has_custom_aggregator": self._aggregator is not None,
            "default_config": self._config,
        }

    def evaluate_pair(self, input_text: str, output_text: str) -> dict[str, Any]:
        """Run all scorers against a single (input, output) pair."""
        scores = [s.score(input_text, output_text) for s in self._scorers]
        if self._aggregator:
            overall = max(0.0, min(1.0, self._aggregator(scores)))
        else:
            overall = sum(scores) / len(scores) if scores else 0.0
        return {
            "overall": overall,
            "step_scores": scores,
            "scorer_results": [
                {"description": s.description, "score": sc}
                for s, sc in zip(self._scorers, scores)
            ],
        }
