"""Evaluation metrics for EvalOps."""

from backend.eval.metrics.base import BaseMetric
from backend.eval.metrics.context_relevance import ContextRelevanceMetric
from backend.eval.metrics.cost_efficiency import CostEfficiencyMetric
from backend.eval.metrics.faithfulness import FaithfulnessMetric
from backend.eval.metrics.guardrail_fp_rate import GuardrailFPRateMetric
from backend.eval.metrics.tool_call_accuracy import ToolCallAccuracyMetric
from backend.eval.metrics.trajectory_coherence import TrajectoryCoherenceMetric

__all__ = [
    "BaseMetric",
    "ContextRelevanceMetric",
    "CostEfficiencyMetric",
    "FaithfulnessMetric",
    "GuardrailFPRateMetric",
    "ToolCallAccuracyMetric",
    "TrajectoryCoherenceMetric",
]

METRIC_REGISTRY: dict[str, type[BaseMetric]] = {
    "faithfulness": FaithfulnessMetric,
    "context_relevance": ContextRelevanceMetric,
    "trajectory_coherence": TrajectoryCoherenceMetric,
    "tool_call_accuracy": ToolCallAccuracyMetric,
    "guardrail_fp_rate": GuardrailFPRateMetric,
    "cost_efficiency": CostEfficiencyMetric,
}


def get_metric(name: str, **config) -> BaseMetric:
    """Instantiate a metric by registry name."""
    cls = METRIC_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown metric '{name}'. Available: {sorted(METRIC_REGISTRY)}"
        )
    return cls(**config)
