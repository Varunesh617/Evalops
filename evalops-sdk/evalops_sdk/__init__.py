"""EvalOps SDK — public API for plugin authors."""

from evalops_sdk.config import ConfigSchemaBuilder, FieldSpec
from evalops_sdk.filters import FilterAuthoring, FilterSpec
from evalops_sdk.metrics import MetricAuthoring, MetricSpec
from evalops_sdk.testing import PluginTestHarness, assert_metric_score, assert_filter_blocked

__all__ = [
    "ConfigSchemaBuilder",
    "FieldSpec",
    "FilterAuthoring",
    "FilterSpec",
    "MetricAuthoring",
    "MetricSpec",
    "PluginTestHarness",
    "assert_filter_blocked",
    "assert_metric_score",
]

__version__ = "0.1.0"
