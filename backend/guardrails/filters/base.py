"""Abstract base class for guardrail filters."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class FilterDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Result returned by every guardrail filter."""

    filter_name: str
    decision: FilterDecision
    score: float  # 0.0 (clean) - 1.0 (worst)
    risk_level: RiskLevel
    details: dict[str, Any] = field(default_factory=dict)
    blocked_by: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return self.decision == FilterDecision.ALLOW

    @property
    def blocked(self) -> bool:
        return self.decision == FilterDecision.BLOCK


@dataclass(slots=True)
class FilterMetrics:
    """Running statistics for a single filter instance."""

    total_checks: int = 0
    total_blocks: int = 0
    total_allows: int = 0
    total_warns: int = 0
    false_positives: int = 0
    total_duration_ms: float = 0.0

    @property
    def block_rate(self) -> float:
        return self.total_blocks / self.total_checks if self.total_checks else 0.0

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.total_blocks if self.total_blocks else 0.0

    @property
    def avg_duration_ms(self) -> float:
        return self.total_duration_ms / self.total_checks if self.total_checks else 0.0


class BaseFilter(ABC):
    """Abstract base for all guardrail filters.

    Subclasses must implement ``_check``.  The public ``check`` method
    wraps ``_check`` with timing, metrics, and logging.
    """

    name: str = "base"

    def __init__(self, *, enabled: bool = True, threshold: float = 0.5) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self._metrics = FilterMetrics()

    def check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        """Run the filter and return a ``FilterResult``."""
        if not self.enabled:
            return self._skip_result()

        start = time.perf_counter()
        result = self._check(input_text, context=context, output=output)
        elapsed_ms = (time.perf_counter() - start) * 1000

        enriched = FilterResult(
            filter_name=result.filter_name,
            decision=result.decision,
            score=result.score,
            risk_level=result.risk_level,
            details=result.details,
            blocked_by=result.blocked_by,
            duration_ms=elapsed_ms,
        )

        self._update_metrics(enriched)
        self._log_result(enriched)
        return enriched

    def mark_false_positive(self) -> None:
        """Mark the most recent block as a false positive."""
        self._metrics.false_positives += 1

    def get_metrics(self) -> dict[str, Any]:
        """Return a snapshot of filter metrics."""
        return {
            "filter": self.name,
            "enabled": self.enabled,
            "threshold": self.threshold,
            "total_checks": self._metrics.total_checks,
            "total_blocks": self._metrics.total_blocks,
            "total_allows": self._metrics.total_allows,
            "total_warns": self._metrics.total_warns,
            "block_rate": round(self._metrics.block_rate, 4),
            "false_positives": self._metrics.false_positives,
            "false_positive_rate": round(self._metrics.false_positive_rate, 4),
            "avg_duration_ms": round(self._metrics.avg_duration_ms, 2),
        }

    def reset_metrics(self) -> None:
        self._metrics = FilterMetrics()

    @abstractmethod
    def _check(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> FilterResult:
        """Implement the actual filter logic."""
        ...

    def _update_metrics(self, result: FilterResult) -> None:
        m = self._metrics
        m.total_checks += 1
        m.total_duration_ms += result.duration_ms
        match result.decision:
            case FilterDecision.BLOCK:
                m.total_blocks += 1
            case FilterDecision.ALLOW:
                m.total_allows += 1
            case FilterDecision.WARN:
                m.total_warns += 1

    def _log_result(self, result: FilterResult) -> None:
        log_fn = logger.warning if result.blocked else logger.debug
        log_fn(
            "guardrail_filter_check",
            filter=self.name,
            decision=result.decision.value,
            score=round(result.score, 3),
            risk_level=result.risk_level.value,
            duration_ms=round(result.duration_ms, 2),
        )

    def _skip_result(self) -> FilterResult:
        return FilterResult(
            filter_name=self.name,
            decision=FilterDecision.ALLOW,
            score=0.0,
            risk_level=RiskLevel.LOW,
            details={"skipped": True},
        )

    def _score_to_risk(self, score: float) -> RiskLevel:
        if score < 0.3:
            return RiskLevel.LOW
        if score < 0.6:
            return RiskLevel.MEDIUM
        if score < 0.85:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL

    def _decide(self, score: float) -> FilterDecision:
        if score >= self.threshold:
            return FilterDecision.BLOCK
        if score >= self.threshold * 0.7:
            return FilterDecision.WARN
        return FilterDecision.ALLOW
