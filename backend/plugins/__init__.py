"""EvalOps plugin ecosystem — loader, registry, discovery, SDK, and marketplace."""

from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRecord, PluginRegistry
from backend.plugins.sdk import (
    FilterPlugin,
    IntegrationPlugin,
    MetricPlugin,
    OptimizerPlugin,
    PluginBase,
)

__all__ = [
    "FilterPlugin",
    "IntegrationPlugin",
    "MetricPlugin",
    "OptimizerPlugin",
    "PluginBase",
    "PluginLoader",
    "PluginRecord",
    "PluginRegistry",
]
