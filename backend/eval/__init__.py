"""EvalOps evaluation engine and metrics."""

from backend.eval.engine import EvalEngine
from backend.eval.metrics import (
    METRIC_REGISTRY,
    BaseMetric,
    ContextRelevanceMetric,
    CostEfficiencyMetric,
    FaithfulnessMetric,
    GuardrailFPRateMetric,
    ToolCallAccuracyMetric,
    TrajectoryCoherenceMetric,
    get_metric,
)
from backend.eval.models import (
    EvalResult,
    MetricResult,
    Step,
    StepScore,
    StepType,
    ToolCall,
    Trajectory,
)

__all__ = [
    "BaseMetric",
    "ContextRelevanceMetric",
    "CostEfficiencyMetric",
    "EvalEngine",
    "EvalResult",
    "FaithfulnessMetric",
    "GuardrailFPRateMetric",
    "METRIC_REGISTRY",
    "MetricResult",
    "Step",
    "StepScore",
    "StepType",
    "ToolCall",
    "ToolCallAccuracyMetric",
    "Trajectory",
    "TrajectoryCoherenceMetric",
    "get_metric",
]
