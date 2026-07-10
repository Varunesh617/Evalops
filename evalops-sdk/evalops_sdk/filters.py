"""Filter authoring helpers for EvalOps plugin authors."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field


class FilterVerdict(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"


class FilterSpec(BaseModel):
    """Declarative specification for a filter plugin."""

    plugin_id: str
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    default_threshold: float = 0.5


@dataclass
class CheckFunction:
    """A single check function with metadata."""

    fn: Callable[[str, str, str], tuple[FilterVerdict, float, dict[str, Any]]]
    description: str = ""


class FilterAuthoring:
    """Helper class for building filter plugins with a fluent API."""

    def __init__(self, spec: FilterSpec) -> None:
        self._spec = spec
        self._checks: list[CheckFunction] = []
        self._config: dict[str, Any] = {}

    @classmethod
    def define(cls, plugin_id: str, name: str) -> FilterAuthoring:
        return cls(FilterSpec(plugin_id=plugin_id, name=name))

    def version(self, version: str) -> FilterAuthoring:
        self._spec.version = version
        return self

    def author(self, author: str) -> FilterAuthoring:
        self._spec.author = author
        return self

    def description(self, description: str) -> FilterAuthoring:
        self._spec.description = description
        return self

    def threshold(self, value: float) -> FilterAuthoring:
        self._spec.default_threshold = value
        return self

    def check(
        self,
        fn: Callable[[str, str, str], tuple[FilterVerdict, float, dict[str, Any]]],
        *,
        description: str = "",
    ) -> FilterAuthoring:
        """Add a check function: (input, context, output) → (verdict, score, details)."""
        self._checks.append(CheckFunction(fn=fn, description=description))
        return self

    def config(self, **kwargs: Any) -> FilterAuthoring:
        self._config.update(kwargs)
        return self

    def build(self) -> dict[str, Any]:
        return {
            "spec": self._spec.model_dump(),
            "checks": [{"description": c.description} for c in self._checks],
            "default_config": self._config,
        }

    def run_checks(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> dict[str, Any]:
        """Run all checks and combine results."""
        results: list[dict[str, Any]] = []
        worst_verdict = FilterVerdict.ALLOW
        max_score = 0.0

        for check in self._checks:
            verdict, score, details = check.fn(input_text, context, output)
            results.append({
                "description": check.description,
                "verdict": verdict.value,
                "score": score,
                "details": details,
            })
            if verdict == FilterVerdict.BLOCK:
                worst_verdict = FilterVerdict.BLOCK
            elif verdict == FilterVerdict.WARN and worst_verdict != FilterVerdict.BLOCK:
                worst_verdict = FilterVerdict.WARN
            max_score = max(max_score, score)

        return {
            "verdict": worst_verdict.value,
            "score": max_score,
            "checks": results,
            "total_checks": len(results),
        }
