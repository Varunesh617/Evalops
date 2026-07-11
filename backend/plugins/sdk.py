"""Plugin SDK — base classes for all EvalOps plugin types."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from backend.eval.models import MetricResult, Step, Trajectory
from backend.guardrails.filters.base import BaseFilter, FilterResult

# ---------------------------------------------------------------------------
# Core plugin base
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PluginMeta:
    """Static metadata every plugin exposes."""

    plugin_id: str
    name: str
    version: str
    author: str
    description: str
    plugin_type: str
    license: str = "MIT"
    homepage: str = ""
    tags: list[str] = field(default_factory=list)
    requires_python: str = ">=3.13"
    dependencies: list[str] = field(default_factory=list)


class PluginBase(abc.ABC):
    """Abstract base for every EvalOps plugin.

    Subclasses must set the class-level attributes or override the
    corresponding properties.  The ``config_schema`` method returns a
    JSON-Schema-compatible dict describing accepted configuration.
    """

    plugin_id: str = "base"
    name: str = "Base Plugin"
    version: str = "0.1.0"
    author: str = ""
    description: str = ""

    def __init__(self) -> None:
        pass

    @abc.abstractmethod
    def config_schema(self) -> dict[str, Any]:
        """Return a JSON-Schema dict describing the plugin's configuration."""
        ...

    def metadata(self) -> PluginMeta:
        """Return structured metadata for this plugin."""
        return PluginMeta(
            plugin_id=self.plugin_id,
            name=self.name,
            version=self.version,
            author=self.author,
            description=self.description,
            plugin_type=type(self).__name__,
        )

    def on_install(self) -> None:
        """Called once when the plugin is first installed."""

    def on_uninstall(self) -> None:
        """Called when the plugin is removed."""

    def on_enable(self) -> None:
        """Called when the plugin is activated."""

    def on_disable(self) -> None:
        """Called when the plugin is deactivated."""


# ---------------------------------------------------------------------------
# Metric plugin
# ---------------------------------------------------------------------------


class MetricPlugin(PluginBase):
    """Base class for custom evaluation metric plugins.

    Wraps the core ``BaseMetric`` interface so plugin authors do not
    need to import internal modules.
    """

    plugin_type: str = "metric"

    @abc.abstractmethod
    def score_step(self, trajectory: Trajectory, step: Step) -> float:
        """Score a single step.  Must return a value in [0, 1]."""
        ...

    def aggregate_steps(self, scores: list[float]) -> float:
        """Combine per-step scores.  Default: simple average."""
        return sum(scores) / len(scores) if scores else 0.0

    def evaluate(self, trajectory: Trajectory) -> MetricResult:
        """Run the metric against a full trajectory."""
        from backend.eval.metrics.base import BaseMetric

        step_scores = []
        scores: list[float] = []
        for step in trajectory.steps:
            score = BaseMetric.clamp(self.score_step(trajectory, step))
            scores.append(score)
            step_scores.append(
                _make_step_score(step.step_id, self.plugin_id, score)
            )
        overall = BaseMetric.clamp(self.aggregate_steps(scores))
        return MetricResult(
            metric_name=self.plugin_id,
            overall_score=overall,
            step_scores=step_scores,
            details=f"{self.name}: {overall:.4f}",
            metadata={"plugin_version": self.version},
        )


# ---------------------------------------------------------------------------
# Filter plugin
# ---------------------------------------------------------------------------


class FilterPlugin(PluginBase):
    """Base class for custom guardrail filter plugins."""

    plugin_type: str = "filter"

    @abc.abstractmethod
    def check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        """Run the filter and return a FilterResult."""
        ...

    def create_filter_instance(self, **kwargs: Any) -> BaseFilter:
        """Return a ``BaseFilter`` subclass wired to this plugin's logic.

        The default implementation wraps the ``check`` method; override
        for richer integration.
        """
        plugin = self

        class _DynamicFilter(BaseFilter):
            name = plugin.plugin_id

            def _check(
                self_inner, input_text: str, *, context: str = "", output: str = ""
            ) -> FilterResult:
                return plugin.check(input_text, context=context, output=output)

        return _DynamicFilter(**kwargs)


# ---------------------------------------------------------------------------
# Optimizer plugin
# ---------------------------------------------------------------------------


class OptimizerPlugin(PluginBase):
    """Base class for custom optimization strategy plugins."""

    plugin_type: str = "optimizer"

    @abc.abstractmethod
    def optimize(
        self,
        search_space: dict[str, Any],
        objective_fn: Any,
        *,
        n_trials: int = 50,
        timeout_seconds: float = 3600.0,
    ) -> dict[str, Any]:
        """Run the optimisation and return the best parameters found."""
        ...

    def suggest_params(
        self, search_space: dict[str, Any], history: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Suggest the next parameter combination to try.

        Override for custom suggest logic.  Default returns the first
        point in the search space.
        """
        return {k: v[0] if isinstance(v, list) else v for k, v in search_space.items()}


# ---------------------------------------------------------------------------
# Integration plugin
# ---------------------------------------------------------------------------


class IntegrationPlugin(PluginBase):
    """Base class for EHR / LLM / external-system integration plugins."""

    plugin_type: str = "integration"

    @abc.abstractmethod
    def connect(self, **kwargs: Any) -> None:
        """Establish a connection to the external system."""
        ...

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Tear down the connection."""
        ...

    @abc.abstractmethod
    def fetch_trajectories(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Pull trajectory data from the external system."""
        ...

    def health_check(self) -> dict[str, Any]:
        """Return connectivity status.  Override for real checks."""
        return {"status": "unknown"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_step_score(step_id: int, metric_name: str, score: float) -> Any:
    from backend.eval.models import StepScore

    return StepScore(step_id=step_id, metric_name=metric_name, score=score)
