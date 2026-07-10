"""Composable guardrail orchestrator.

Executes a pipeline of filter instances on input and returns a unified
pass/fail result with ``blocked_by`` metadata.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from .compounding_analyzer import CompoundingAnalyzer, FPOverlapReport
from .filters.base import BaseFilter, FilterDecision, FilterMetrics, FilterResult, RiskLevel

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class StackResult:
    """Aggregated result from the full guardrail stack."""

    passed: bool
    decision: FilterDecision
    score: float
    risk_level: RiskLevel
    blocked_by: list[str]
    filter_results: list[FilterResult]
    total_duration_ms: float
    filters_run: int
    filters_blocked: int
    fp_overlap_report: FPOverlapReport | None = None


@dataclass(slots=True)
class GuardrailStack:
    """Composable guardrail orchestrator.

    Accepts a list of filter instances, runs them in sequence, and returns
    an aggregated pass/fail result.

    Usage::

        stack = GuardrailStack(filters=[
            PromptInjectionFilter(threshold=0.6),
            PIIFilter(hipaa_mode=True),
            ToxicityFilter(threshold=0.5),
        ])
        result = stack.run("user input here")
        if result.blocked:
            print(f"Blocked by: {result.blocked_by}")
    """

    filters: list[BaseFilter] = field(default_factory=list)
    fail_fast: bool = False
    compute_fp_overlap: bool = True
    _run_count: int = field(default=0, init=False, repr=False)
    _total_blocks: int = field(default=0, init=False, repr=False)
    _total_allows: int = field(default=0, init=False, repr=False)
    _analyzer: CompoundingAnalyzer = field(default_factory=CompoundingAnalyzer, init=False, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_filter(self, f: BaseFilter) -> None:
        self.filters.append(f)

    def remove_filter(self, name: str) -> bool:
        before = len(self.filters)
        self.filters = [f for f in self.filters if f.name != name]
        return len(self.filters) < before

    def get_filter(self, name: str) -> BaseFilter | None:
        for f in self.filters:
            if f.name == name:
                return f
        return None

    def run(
        self, input_text: str, *, context: str = "", output: str = ""
    ) -> StackResult:
        """Execute all filters on the input and return aggregated result."""
        self._run_count += 1
        start = time.perf_counter()

        results: list[FilterResult] = []
        blocked_filters: list[str] = []

        for f in self.filters:
            result = f.check(input_text, context=context, output=output)
            results.append(result)
            if result.blocked:
                blocked_filters.extend(result.blocked_by)
            elif self.fail_fast and result.blocked:
                break

        elapsed_ms = (time.perf_counter() - start) * 1000
        overall_passed = all(r.passed for r in results)
        overall_decision = FilterDecision.ALLOW if overall_passed else FilterDecision.BLOCK
        max_score = max((r.score for r in results), default=0.0)
        worst_risk = self._worst_risk(results)

        if overall_passed:
            self._total_allows += 1
        else:
            self._total_blocks += 1

        fp_report = None
        if self.compute_fp_overlap and results:
            fp_report = self._analyzer.analyze(results)

        stack_result = StackResult(
            passed=overall_passed,
            decision=overall_decision,
            score=round(max_score, 3),
            risk_level=worst_risk,
            blocked_by=blocked_filters,
            filter_results=results,
            total_duration_ms=round(elapsed_ms, 2),
            filters_run=len(results),
            filters_blocked=sum(1 for r in results if r.blocked),
            fp_overlap_report=fp_report,
        )

        self._log_stack_result(stack_result)
        return stack_result

    def get_stack_metrics(self) -> dict[str, Any]:
        """Return aggregate stack-level metrics."""
        block_rate = self._total_blocks / self._run_count if self._run_count else 0.0
        return {
            "total_runs": self._run_count,
            "total_blocks": self._total_blocks,
            "total_allows": self._total_allows,
            "block_rate": round(block_rate, 4),
            "filter_count": len(self.filters),
            "filter_metrics": [f.get_metrics() for f in self.filters],
        }

    def reset_all_metrics(self) -> None:
        self._run_count = 0
        self._total_blocks = 0
        self._total_allows = 0
        self._analyzer.reset()
        for f in self.filters:
            f.reset_metrics()

    def mark_false_positive(self, filter_name: str) -> bool:
        """Forward a false-positive mark to the named filter."""
        f = self.get_filter(filter_name)
        if f:
            f.mark_false_positive()
            self._analyzer.record_false_positive(filter_name)
            return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _worst_risk(results: list[FilterResult]) -> RiskLevel:
        if not results:
            return RiskLevel.LOW
        order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        worst_idx = 0
        for r in results:
            idx = order.index(r.risk_level)
            if idx > worst_idx:
                worst_idx = idx
        return order[worst_idx]

    def _log_stack_result(self, result: StackResult) -> None:
        if result.passed:
            logger.debug(
                "guardrail_stack_pass",
                filters_run=result.filters_run,
                max_score=result.score,
                duration_ms=result.total_duration_ms,
            )
        else:
            logger.warning(
                "guardrail_stack_block",
                blocked_by=result.blocked_by,
                filters_run=result.filters_run,
                filters_blocked=result.filters_blocked,
                max_score=result.score,
                risk_level=result.risk_level.value,
                duration_ms=result.total_duration_ms,
            )
