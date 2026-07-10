"""Historical failure analysis — aggregate patterns, trends, and correlations.

Tracks failure data over time and produces reports identifying recurring
root causes, trend direction, and clustering patterns.
"""

from __future__ import annotations

import enum
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class TrendDirection(enum.StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """A single failure event for historical tracking."""

    trace_id: str
    timestamp: datetime
    root_cause_step: str
    failure_mode: str
    severity: str
    score: float
    pipeline_id: str = ""
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FrequencyBucket:
    """Failure count in a time bucket."""

    period_start: datetime
    period_end: datetime
    count: int
    failure_modes: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecurringPattern:
    """A pattern that repeats across multiple failures."""

    failure_mode: str
    step_name: str
    occurrences: int
    percentage: float
    avg_score: float
    sample_trace_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Correlation:
    """A detected correlation between failure patterns."""

    dimension: str  # "time", "pipeline", "model", "step"
    description: str
    strength: float  # 0.0–1.0
    detail: str = ""


@dataclass(slots=True)
class HistoricalReport:
    """Aggregated historical failure analysis."""

    report_id: str = field(default_factory=lambda: f"hist-{uuid.uuid4().hex[:12]}")
    total_failures: int = 0
    time_range_start: datetime | None = None
    time_range_end: datetime | None = None
    frequency: list[FrequencyBucket] = field(default_factory=list)
    recurring_patterns: list[RecurringPattern] = field(default_factory=list)
    trend: TrendDirection = TrendDirection.STABLE
    trend_confidence: float = 0.0
    correlations: list[Correlation] = field(default_factory=list)
    failure_mode_distribution: dict[str, int] = field(default_factory=dict)
    step_distribution: dict[str, int] = field(default_factory=dict)
    avg_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "total_failures": self.total_failures,
            "time_range": {
                "start": self.time_range_start.isoformat() if self.time_range_start else None,
                "end": self.time_range_end.isoformat() if self.time_range_end else None,
            },
            "frequency": [
                {
                    "period_start": f.period_start.isoformat(),
                    "period_end": f.period_end.isoformat(),
                    "count": f.count,
                    "failure_modes": f.failure_modes,
                }
                for f in self.frequency
            ],
            "recurring_patterns": [
                {
                    "failure_mode": p.failure_mode,
                    "step_name": p.step_name,
                    "occurrences": p.occurrences,
                    "percentage": round(p.percentage, 2),
                    "avg_score": round(p.avg_score, 4),
                    "sample_trace_ids": p.sample_trace_ids,
                }
                for p in self.recurring_patterns
            ],
            "trend": str(self.trend),
            "trend_confidence": round(self.trend_confidence, 3),
            "correlations": [
                {
                    "dimension": c.dimension,
                    "description": c.description,
                    "strength": round(c.strength, 3),
                    "detail": c.detail,
                }
                for c in self.correlations
            ],
            "failure_mode_distribution": self.failure_mode_distribution,
            "step_distribution": self.step_distribution,
            "avg_score": round(self.avg_score, 4),
        }


# ---------------------------------------------------------------------------
# Historical analyzer
# ---------------------------------------------------------------------------


class HistoricalAnalyzer:
    """Aggregates failure records and produces historical analysis reports.

    The analyzer works with in-memory records.  In production, failure records
    would be persisted to a database and loaded into the analyzer.
    """

    def __init__(self) -> None:
        self._records: list[FailureRecord] = []

    def add_record(self, record: FailureRecord) -> None:
        """Ingest a single failure record."""
        self._records.append(record)

    def add_records(self, records: list[FailureRecord]) -> None:
        """Ingest a batch of failure records."""
        self._records.extend(records)

    @property
    def record_count(self) -> int:
        return len(self._records)

    def analyse(
        self,
        *,
        time_window_days: int = 30,
        bucket_days: int = 1,
    ) -> HistoricalReport:
        """Produce a :class:`HistoricalReport` from all ingested records.

        Parameters
        ----------
        time_window_days:
            Only consider records from the last N days.
        bucket_days:
            Size of each time bucket for frequency analysis.
        """
        if not self._records:
            return HistoricalReport(total_failures=0)

        cutoff = datetime.now() - timedelta(days=time_window_days)
        recent = [r for r in self._records if r.timestamp >= cutoff]

        if not recent:
            return HistoricalReport(total_failures=0)

        sorted_records = sorted(recent, key=lambda r: r.timestamp)

        # Time range
        time_start = sorted_records[0].timestamp
        time_end = sorted_records[-1].timestamp

        # Frequency buckets
        frequency = self._build_frequency(sorted_records, time_start, bucket_days)

        # Distributions
        mode_dist = self._failure_mode_distribution(sorted_records)
        step_dist = self._step_distribution(sorted_records)

        # Average score
        avg_score = statistics.mean(r.score for r in sorted_records)

        # Recurring patterns
        patterns = self._find_recurring_patterns(sorted_records)

        # Trend analysis
        trend, trend_confidence = self._compute_trend(frequency)

        # Correlation analysis
        correlations = self._find_correlations(sorted_records)

        report = HistoricalReport(
            total_failures=len(recent),
            time_range_start=time_start,
            time_range_end=time_end,
            frequency=frequency,
            recurring_patterns=patterns,
            trend=trend,
            trend_confidence=trend_confidence,
            correlations=correlations,
            failure_mode_distribution=mode_dist,
            step_distribution=step_dist,
            avg_score=avg_score,
        )

        logger.info(
            "historical_analysis_complete",
            total_failures=len(recent),
            trend=str(trend),
            pattern_count=len(patterns),
            correlation_count=len(correlations),
        )
        return report

    # -- frequency -----------------------------------------------------------

    @staticmethod
    def _build_frequency(
        records: list[FailureRecord],
        time_start: datetime,
        bucket_days: int,
    ) -> list[FrequencyBucket]:
        """Group records into time buckets."""
        buckets: dict[datetime, FrequencyBucket] = {}

        for record in records:
            days_since_start = (record.timestamp - time_start).days
            bucket_index = days_since_start // bucket_days
            bucket_start = time_start + timedelta(days=bucket_index * bucket_days)
            bucket_end = bucket_start + timedelta(days=bucket_days)

            if bucket_start not in buckets:
                buckets[bucket_start] = FrequencyBucket(
                    period_start=bucket_start,
                    period_end=bucket_end,
                    count=0,
                )

            bucket = buckets[bucket_start]
            bucket.count += 1
            bucket.failure_modes[record.failure_mode] = (
                bucket.failure_modes.get(record.failure_mode, 0) + 1
            )

        return sorted(buckets.values(), key=lambda b: b.period_start)

    # -- distributions -------------------------------------------------------

    @staticmethod
    def _failure_mode_distribution(records: list[FailureRecord]) -> dict[str, int]:
        counter: Counter[str] = Counter(r.failure_mode for r in records)
        return dict(counter.most_common())

    @staticmethod
    def _step_distribution(records: list[FailureRecord]) -> dict[str, int]:
        counter: Counter[str] = Counter(r.root_cause_step for r in records)
        return dict(counter.most_common())

    # -- recurring patterns --------------------------------------------------

    @staticmethod
    def _find_recurring_patterns(records: list[FailureRecord]) -> list[RecurringPattern]:
        """Identify failure patterns that repeat across multiple traces."""
        groups: dict[tuple[str, str], list[FailureRecord]] = defaultdict(list)
        for r in records:
            groups[(r.failure_mode, r.root_cause_step)].append(r)

        total = len(records)
        patterns: list[RecurringPattern] = []

        for (mode, step), group in sorted(
            groups.items(), key=lambda kv: len(kv[1]), reverse=True
        ):
            if len(group) < 2:
                continue

            patterns.append(
                RecurringPattern(
                    failure_mode=mode,
                    step_name=step,
                    occurrences=len(group),
                    percentage=round(len(group) / total * 100, 2),
                    avg_score=round(statistics.mean(r.score for r in group), 4),
                    sample_trace_ids=[r.trace_id for r in group[:5]],
                )
            )

        return patterns[:10]  # top 10

    # -- trend analysis ------------------------------------------------------

    @staticmethod
    def _compute_trend(
        frequency: list[FrequencyBucket],
    ) -> tuple[TrendDirection, float]:
        """Determine trend direction from bucket counts.

        Uses a simple linear regression slope on the bucket counts.
        """
        if len(frequency) < 3:
            return TrendDirection.STABLE, 0.0

        counts = [b.count for b in frequency]
        n = len(counts)
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(counts)

        numerator = sum((i - x_mean) * (c - y_mean) for i, c in enumerate(counts))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return TrendDirection.STABLE, 0.0

        slope = numerator / denominator

        # Normalise slope relative to average count
        if y_mean == 0:
            return TrendDirection.STABLE, 0.0

        normalised_slope = slope / y_mean

        # Thresholds
        if normalised_slope < -0.05:
            direction = TrendDirection.IMPROVING
        elif normalised_slope > 0.05:
            direction = TrendDirection.DEGRADING
        else:
            direction = TrendDirection.STABLE

        # Confidence based on how many buckets and how linear the data is
        confidence = min(1.0, abs(normalised_slope) * 5 + (n - 3) * 0.1)

        return direction, round(confidence, 3)

    # -- correlations --------------------------------------------------------

    @staticmethod
    def _find_correlations(records: list[FailureRecord]) -> list[Correlation]:
        """Detect clustering patterns across time, pipeline, model, and step."""
        correlations: list[Correlation] = []

        # Time-of-day clustering
        hour_counts: Counter[int] = Counter(r.timestamp.hour for r in records)
        if hour_counts:
            most_common_hour, most_common_count = hour_counts.most_common(1)[0]
            total = len(records)
            if most_common_count / total > 0.3:
                correlations.append(
                    Correlation(
                        dimension="time",
                        description=f"Failures cluster at hour {most_common_hour:02d}:00",
                        strength=round(most_common_count / total, 3),
                        detail=(
                            f"{most_common_count}/{total} failures "
                            f"({most_common_count / total:.0%}) occurred "
                            f"during hour {most_common_hour:02d}:00"
                        ),
                    )
                )

        # Pipeline clustering
        pipeline_counts: Counter[str] = Counter(
            r.pipeline_id for r in records if r.pipeline_id
        )
        if pipeline_counts:
            top_pipeline, top_count = pipeline_counts.most_common(1)[0]
            total = len(records)
            if top_count / total > 0.4:
                correlations.append(
                    Correlation(
                        dimension="pipeline",
                        description=f"Pipeline '{top_pipeline}' has disproportionate failures",
                        strength=round(top_count / total, 3),
                        detail=f"{top_count}/{total} failures from pipeline '{top_pipeline}'",
                    )
                )

        # Model clustering
        model_counts: Counter[str] = Counter(r.model for r in records if r.model)
        if model_counts:
            top_model, top_count = model_counts.most_common(1)[0]
            total = len(records)
            if top_count / total > 0.5:
                correlations.append(
                    Correlation(
                        dimension="model",
                        description=f"Model '{top_model}' dominates failures",
                        strength=round(top_count / total, 3),
                        detail=f"{top_count}/{total} failures involve model '{top_model}'",
                    )
                )

        # Step clustering (same step failing repeatedly)
        step_counts: Counter[str] = Counter(r.root_cause_step for r in records)
        if step_counts:
            top_step, top_count = step_counts.most_common(1)[0]
            total = len(records)
            if top_count / total > 0.5:
                correlations.append(
                    Correlation(
                        dimension="step",
                        description=f"Step '{top_step}' is the dominant failure point",
                        strength=round(top_count / total, 3),
                        detail=f"{top_count}/{total} failures root-caused to step '{top_step}'",
                    )
                )

        return sorted(correlations, key=lambda c: c.strength, reverse=True)
