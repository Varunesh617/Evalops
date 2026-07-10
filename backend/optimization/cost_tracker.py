"""Cost tracking, aggregation, and forecasting.

Tracks costs per pipeline, per model, and per time period. Provides
aggregation by multiple dimensions, basic forecasting, and anomaly alerts.
"""

from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

_DEFAULT_COST_DIR = Path("data/costs")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CostEntry(BaseModel):
    """A single cost record."""

    entry_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    pipeline_id: str
    model: str
    user_id: str = ""
    cost_usd: float = Field(ge=0.0)
    tokens_used: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CostBucket(BaseModel):
    """Aggregated cost for a dimension value."""

    label: str
    total_cost_usd: float = 0.0
    entry_count: int = 0
    avg_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    total_tokens: int = 0


class CostForecast(BaseModel):
    """Forecast for future cost over a time horizon."""

    period_start: datetime
    period_end: datetime
    projected_cost_usd: float
    confidence: float = Field(ge=0.0, le=1.0)
    basis_points: int = Field(description="Number of historical data points used")


class CostAnomaly(BaseModel):
    """Detected cost anomaly."""

    entry_id: str
    pipeline_id: str
    model: str
    cost_usd: float
    expected_cost_usd: float
    deviation_ratio: float
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CostReport(BaseModel):
    """Full cost report with breakdowns and forecasts."""

    total_cost_usd: float = 0.0
    total_entries: int = 0
    period_start: datetime | None = None
    period_end: datetime | None = None
    by_pipeline: list[CostBucket] = Field(default_factory=list)
    by_model: list[CostBucket] = Field(default_factory=list)
    by_user: list[CostBucket] = Field(default_factory=list)
    daily_costs: list[dict[str, Any]] = Field(default_factory=list)
    forecasts: list[CostForecast] = Field(default_factory=list)
    anomalies: list[CostAnomaly] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Track and analyze costs across pipelines and models.

    Usage::

        tracker = CostTracker()
        tracker.record_cost(pipeline_id="p-1", model="gpt-4o", cost_usd=0.05)
        report = tracker.get_report(pipeline_id="p-1", days=30)
    """

    def __init__(self, cost_dir: Path | str = _DEFAULT_COST_DIR) -> None:
        self._dir = Path(cost_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._entries = self._load_entries()

    # -- Recording ----------------------------------------------------------

    def record_cost(
        self,
        *,
        pipeline_id: str,
        model: str,
        cost_usd: float,
        user_id: str = "",
        tokens_used: int = 0,
        latency_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> CostEntry:
        """Record a single cost entry."""
        entry = CostEntry(
            pipeline_id=pipeline_id,
            model=model,
            user_id=user_id,
            cost_usd=cost_usd,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )
        self._entries.append(entry)
        self._persist_entries()
        return entry

    def record_batch(self, entries: list[CostEntry]) -> list[CostEntry]:
        """Record multiple cost entries at once."""
        self._entries.extend(entries)
        self._persist_entries()
        return entries

    # -- Reporting ----------------------------------------------------------

    def get_report(
        self,
        *,
        pipeline_id: str | None = None,
        model: str | None = None,
        user_id: str | None = None,
        days: int = 30,
        forecast_days: int = 7,
        anomaly_threshold: float = 2.0,
    ) -> CostReport:
        """Generate a cost report with breakdowns, forecasts, and anomaly detection."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        filtered = [e for e in self._entries if e.timestamp >= cutoff]

        if pipeline_id:
            filtered = [e for e in filtered if e.pipeline_id == pipeline_id]
        if model:
            filtered = [e for e in filtered if e.model == model]
        if user_id:
            filtered = [e for e in filtered if e.user_id == user_id]

        total = sum(e.cost_usd for e in filtered)
        period_start = min((e.timestamp for e in filtered), default=None)
        period_end = max((e.timestamp for e in filtered), default=None)

        by_pipeline = _aggregate(filtered, "pipeline_id")
        by_model = _aggregate(filtered, "model")
        by_user = _aggregate(filtered, "user_id")
        daily = _aggregate_by_day(filtered)

        forecasts = _forecast(daily, horizon_days=forecast_days)
        anomalies = _detect_anomalies(filtered, threshold=anomaly_threshold)

        report = CostReport(
            total_cost_usd=total,
            total_entries=len(filtered),
            period_start=period_start,
            period_end=period_end,
            by_pipeline=by_pipeline,
            by_model=by_model,
            by_user=by_user,
            daily_costs=daily,
            forecasts=forecasts,
            anomalies=anomalies,
        )

        logger.info(
            "cost_tracker.report_generated",
            total_cost=round(total, 4),
            entries=len(filtered),
            anomaly_count=len(anomalies),
        )
        return report

    def aggregate_costs(
        self,
        *,
        dimension: str,
        pipeline_id: str | None = None,
        days: int = 30,
    ) -> list[CostBucket]:
        """Aggregate costs by a single dimension (pipeline, model, user, etc.)."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        filtered = [e for e in self._entries if e.timestamp >= cutoff]
        if pipeline_id:
            filtered = [e for e in filtered if e.pipeline_id == pipeline_id]
        return _aggregate(filtered, dimension)

    # -- Internal -----------------------------------------------------------

    def _entries_path(self) -> Path:
        return self._dir / "entries.json"

    def _load_entries(self) -> list[CostEntry]:
        path = self._entries_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [CostEntry.model_validate(e) for e in data]

    def _persist_entries(self) -> None:
        """Persist entries to disk. Keeps at most 50k entries."""
        to_save = self._entries[-50_000:]
        path = self._entries_path()
        path.write_text(
            json.dumps([e.model_dump(mode="json") for e in to_save], indent=2, default=str),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate(entries: list[CostEntry], field: str) -> list[CostBucket]:
    """Group entries by a field and compute per-group metrics."""
    groups: dict[str, list[CostEntry]] = defaultdict(list)
    for entry in entries:
        key = getattr(entry, field, "") or "(none)"
        groups[str(key)].append(entry)

    buckets: list[CostBucket] = []
    for label, group in sorted(groups.items()):
        total = sum(e.cost_usd for e in group)
        count = len(group)
        tokens = sum(e.tokens_used for e in group)
        avg_latency = sum(e.latency_ms for e in group) / count if count else 0.0
        buckets.append(
            CostBucket(
                label=label,
                total_cost_usd=total,
                entry_count=count,
                avg_cost_usd=total / count if count else 0.0,
                avg_latency_ms=avg_latency,
                total_tokens=tokens,
            )
        )
    buckets.sort(key=lambda b: b.total_cost_usd, reverse=True)
    return buckets


def _aggregate_by_day(entries: list[CostEntry]) -> list[dict[str, Any]]:
    """Group entries by calendar day and return sorted daily totals."""
    daily: dict[str, float] = defaultdict(float)
    for entry in entries:
        day_key = entry.timestamp.strftime("%Y-%m-%d")
        daily[day_key] += entry.cost_usd

    result = [{"date": k, "cost_usd": round(v, 6)} for k, v in sorted(daily.items())]
    return result


# ---------------------------------------------------------------------------
# Forecasting (simple linear extrapolation)
# ---------------------------------------------------------------------------


def _forecast(
    daily: list[dict[str, Any]], *, horizon_days: int = 7
) -> list[CostForecast]:
    """Project future costs using linear regression on daily data."""
    if len(daily) < 3:
        return []

    costs = [d["cost_usd"] for d in daily]
    n = len(costs)
    x_mean = (n - 1) / 2
    y_mean = sum(costs) / n

    num = sum((i - x_mean) * (costs[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0.0
    intercept = y_mean - slope * x_mean

    forecasts: list[CostForecast] = []
    last_date = datetime.strptime(daily[-1]["date"], "%Y-%m-%d").replace(tzinfo=UTC)

    # Simple confidence based on R²
    ss_res = sum((costs[i] - (intercept + slope * i)) ** 2 for i in range(n))
    ss_tot = sum((costs[i] - y_mean) ** 2 for i in range(n))
    r_squared = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot != 0 else 0.0

    for day_offset in range(1, horizon_days + 1):
        projected_index = n + day_offset - 1
        projected = max(0.0, intercept + slope * projected_index)
        period_start = last_date + timedelta(days=day_offset - 1)
        period_end = last_date + timedelta(days=day_offset)
        forecasts.append(
            CostForecast(
                period_start=period_start,
                period_end=period_end,
                projected_cost_usd=round(projected, 6),
                confidence=round(min(r_squared, 1.0), 3),
                basis_points=n,
            )
        )

    return forecasts


# ---------------------------------------------------------------------------
# Anomaly detection (Z-score based)
# ---------------------------------------------------------------------------


def _detect_anomalies(
    entries: list[CostEntry], *, threshold: float = 2.0
) -> list[CostAnomaly]:
    """Detect cost anomalies using per-model Z-score."""
    if len(entries) < 5:
        return []

    # Group by model
    model_entries: dict[str, list[CostEntry]] = defaultdict(list)
    for entry in entries:
        model_entries[entry.model].append(entry)

    anomalies: list[CostAnomaly] = []
    for model, group in model_entries.items():
        costs = [e.cost_usd for e in group]
        mean = sum(costs) / len(costs)
        variance = sum((c - mean) ** 2 for c in costs) / len(costs)
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0:
            continue

        for entry in group:
            z_score = (entry.cost_usd - mean) / std
            if abs(z_score) > threshold:
                anomalies.append(
                    CostAnomaly(
                        entry_id=entry.entry_id,
                        pipeline_id=entry.pipeline_id,
                        model=model,
                        cost_usd=entry.cost_usd,
                        expected_cost_usd=round(mean, 6),
                        deviation_ratio=round(z_score, 3),
                    )
                )

    anomalies.sort(key=lambda a: abs(a.deviation_ratio), reverse=True)
    return anomalies
