"""False-positive compounding analyzer.

Tracks FP overlap across filters, models filter dependencies, and calculates
the effective FP rate for the full filter stack.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import structlog

from .filters.base import FilterResult

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FilterFPStats:
    """Per-filter false-positive statistics."""

    filter_name: str
    total_blocks: int = 0
    false_positives: int = 0

    @property
    def fp_rate(self) -> float:
        return self.false_positives / self.total_blocks if self.total_blocks else 0.0


@dataclass(frozen=True, slots=True)
class FPOverlapReport:
    """Analysis of how false positives compound across the filter stack."""

    total_runs: int
    total_blocks: int
    effective_fp_rate: float
    individual_fp_rates: dict[str, float]
    overlap_matrix: dict[str, dict[str, int]]
    independence_assumption_rate: float
    compounding_factor: float
    recommendations: list[str]


@dataclass(slots=True)
class CompoundingAnalyzer:
    """Track and analyze false-positive compounding across a filter stack.

    The analyzer maintains running statistics to answer:
    - How often do multiple filters block the same input?
    - What is the effective FP rate vs. the theoretical independent rate?
    - Which filters have correlated false positives?
    - What threshold adjustments would reduce FPs with minimal recall loss?
    """

    _run_count: int = field(default=0, init=False)
    _block_count: int = field(default=0, init=False)
    _fp_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int), init=False)
    _block_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int), init=False)
    _co_block_counts: dict[tuple[str, str], int] = field(
        default_factory=lambda: defaultdict(int), init=False
    )
    _run_blocking_filters: list[list[str]] = field(default_factory=list, init=False)

    def analyze(self, results: list[FilterResult]) -> FPOverlapReport:
        """Analyze a set of filter results for FP overlap patterns."""
        self._run_count += 1
        blocking = [r.filter_name for r in results if r.blocked]

        if blocking:
            self._block_count += 1
            for name in blocking:
                self._block_counts[name] += 1
            for i, a in enumerate(blocking):
                for b in blocking[i + 1 :]:
                    key = tuple(sorted((a, b)))
                    self._co_block_counts[key] += 1

        self._run_blocking_filters.append(blocking)
        return self._build_report()

    def record_false_positive(self, filter_name: str) -> None:
        """Record that a specific filter's block was a false positive."""
        self._fp_counts[filter_name] += 1

    def reset(self) -> None:
        self._run_count = 0
        self._block_count = 0
        self._fp_counts.clear()
        self._block_counts.clear()
        self._co_block_counts.clear()
        self._run_blocking_filters.clear()

    def get_overlap_probability(self, a: str, b: str) -> float:
        """P(A and B block) given individual block rates."""
        key = tuple(sorted((a, b)))
        if self._run_count == 0:
            return 0.0
        return self._co_block_counts.get(key, 0) / self._run_count

    def get_conditional_probability(self, a: str, b: str) -> float:
        """P(B blocks | A blocks) -- measures dependency between filters."""
        a_blocks = self._block_counts.get(a, 0)
        if a_blocks == 0:
            return 0.0
        key = tuple(sorted((a, b)))
        co = self._co_block_counts.get(key, 0)
        return co / a_blocks

    def get_effective_independence_rate(self) -> float:
        """Calculate what the FP rate would be if all filters were independent.

        P(at least one FP) = 1 - product(1 - p_i) for each filter i.
        """
        rates = self._individual_fp_rates()
        if not rates:
            return 0.0
        prob_no_fp = 1.0
        for rate in rates.values():
            prob_no_fp *= max(0.0, 1.0 - rate)
        return 1.0 - prob_no_fp

    def suggest_threshold_adjustments(self) -> list[str]:
        """Suggest threshold tweaks to reduce FPs with minimal recall loss."""
        suggestions: list[str] = []
        for name, fp_rate in self._individual_fp_rates().items():
            if fp_rate > 0.3:
                suggestions.append(
                    f"'{name}' has FP rate {fp_rate:.1%} -- consider raising threshold by 0.05-0.10"
                )
            elif fp_rate > 0.15:
                suggestions.append(
                    f"'{name}' has moderate FP rate {fp_rate:.1%} -- consider raising threshold by 0.03-0.05"
                )

        for (a, b), co_count in self._co_block_counts.items():
            if co_count < 3:
                continue
            p_a = self._block_counts.get(a, 0) / self._run_count if self._run_count else 0
            p_b = self._block_counts.get(b, 0) / self._run_count if self._run_count else 0
            p_ab = co_count / self._run_count
            expected_independent = p_a * p_b
            if expected_independent > 0 and p_ab > expected_independent * 2:
                suggestions.append(
                    f"'{a}' and '{b}' co-block {co_count}x (expected ~{expected_independent * self._run_count:.1f}x "
                    f"under independence) -- strong dependency, consider deduplication"
                )

        if not suggestions:
            suggestions.append("No adjustment recommendations -- FP rates are within acceptable bounds")

        return suggestions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _individual_fp_rates(self) -> dict[str, float]:
        rates: dict[str, float] = {}
        for name, blocks in self._block_counts.items():
            fps = self._fp_counts.get(name, 0)
            rates[name] = fps / blocks if blocks else 0.0
        return rates

    def _build_report(self) -> FPOverlapReport:
        individual_rates = self._individual_fp_rates()
        independence_rate = self.get_effective_independence_rate()

        total_fps = sum(self._fp_counts.values())
        effective_fp_rate = total_fps / self._block_count if self._block_count else 0.0

        compounding = (
            effective_fp_rate / independence_rate
            if independence_rate > 0
            else 1.0
        )

        overlap_matrix: dict[str, dict[str, int]] = {}
        for (a, b), count in self._co_block_counts.items():
            overlap_matrix.setdefault(a, {})[b] = count
            overlap_matrix.setdefault(b, {})[a] = count

        return FPOverlapReport(
            total_runs=self._run_count,
            total_blocks=self._block_count,
            effective_fp_rate=round(effective_fp_rate, 4),
            individual_fp_rates={k: round(v, 4) for k, v in individual_rates.items()},
            overlap_matrix=overlap_matrix,
            independence_assumption_rate=round(independence_rate, 4),
            compounding_factor=round(compounding, 3),
            recommendations=self.suggest_threshold_adjustments(),
        )
