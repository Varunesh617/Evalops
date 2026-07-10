"""Regression detector for evaluation scores.

Compares current eval scores against a baseline, detects statistically
significant drops, and emits alerts when regressions are found.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

import numpy as np
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums & models
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    """Severity level for a detected regression."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RegressionAlert(BaseModel):
    """A single regression alert."""

    metric_name: str
    baseline_mean: float
    baseline_std: float
    current_mean: float
    current_std: float
    z_score: float
    p_value: float
    absolute_drop: float
    relative_drop_pct: float
    severity: Severity
    message: str


class RegressionReport(BaseModel):
    """Full regression analysis report."""

    baseline_sample_size: int
    current_sample_size: int
    baseline_run_id: str | None = None
    current_run_id: str | None = None
    alerts: list[RegressionAlert] = Field(default_factory=list)
    metrics_checked: list[str] = Field(default_factory=list)
    has_regression: bool = False
    critical_count: int = 0
    warning_count: int = 0
    analysis_duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def z_test_two_sample(
    baseline: np.ndarray,
    current: np.ndarray,
) -> tuple[float, float]:
    """Two-sample z-test (Welch's, known-ish variance from samples).

    Returns (z_score, p_value).
    A large positive z_score means the current mean is *lower* than baseline
    (we test for regression = drop).
    """
    n_b, n_c = len(baseline), len(current)
    if n_b < 2 or n_c < 2:
        return 0.0, 1.0

    mean_b = float(np.mean(baseline))
    mean_c = float(np.mean(current))
    var_b = float(np.var(baseline, ddof=1))
    var_c = float(np.var(current, ddof=1))

    se = np.sqrt(var_b / n_b + var_c / n_c)
    if se == 0:
        return 0.0, 1.0

    # z > 0 means current is lower (regression direction)
    z = (mean_b - mean_c) / se

    # one-sided p-value (testing for drop)
    from scipy import stats

    p_value = float(1.0 - stats.norm.cdf(z))
    return float(z), p_value


def simple_t_test(
    baseline: np.ndarray,
    current: np.ndarray,
) -> tuple[float, float]:
    """Fallback t-test when scipy is unavailable.

    Uses Welch's t-test approximation.
    """
    n_b, n_c = len(baseline), len(current)
    if n_b < 2 or n_c < 2:
        return 0.0, 1.0

    mean_b = float(np.mean(baseline))
    mean_c = float(np.mean(current))
    var_b = float(np.var(baseline, ddof=1))
    var_c = float(np.var(current, ddof=1))

    se = np.sqrt(var_b / n_b + var_c / n_c)
    if se == 0:
        return 0.0, 1.0

    t_stat = (mean_b - mean_c) / se
    # Approximate p-value via normal for large samples
    z_approx = t_stat
    # Using error function approximation for norm.cdf
    p_value = 0.5 * (1.0 - _erf(z_approx / np.sqrt(2)))
    return float(z_approx), float(max(0.0, p_value))


def _erf(x: float) -> float:
    """Approximation of the error function (no scipy needed)."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x_abs = abs(x)
    t = 1.0 / (1.0 + p * x_abs)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x_abs * x_abs)
    return sign * y


def _classify_severity(
    p_value: float,
    relative_drop: float,
    *,
    warning_threshold: float = 0.05,
    critical_threshold: float = 0.01,
    critical_drop_pct: float = 10.0,
) -> Severity:
    """Classify alert severity based on p-value and drop magnitude."""
    if p_value < critical_threshold and relative_drop >= critical_drop_pct:
        return Severity.CRITICAL
    if p_value < warning_threshold:
        return Severity.WARNING
    return Severity.INFO


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class RegressionDetector:
    """Detect statistically significant regressions in eval scores.

    Usage::

        detector = RegressionDetector(
            baseline_scores={"faithfulness": baseline_arr, "relevance": rel_arr},
            current_scores={"faithfulness": current_arr, "relevance": rel_arr},
        )
        report = detector.analyze()
        if report.has_regression:
            for alert in report.alerts:
                print(alert.message)
    """

    def __init__(
        self,
        baseline_scores: dict[str, np.ndarray],
        current_scores: dict[str, np.ndarray],
        *,
        z_threshold: float = 1.645,
        p_threshold: float = 0.05,
        relative_drop_threshold_pct: float = 5.0,
        baseline_run_id: str | None = None,
        current_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._baseline = baseline_scores
        self._current = current_scores
        self._z_threshold = z_threshold
        self._p_threshold = p_threshold
        self._rel_drop_threshold = relative_drop_threshold_pct
        self._baseline_run_id = baseline_run_id
        self._current_run_id = current_run_id
        self._metadata = metadata or {}

    def analyze(self) -> RegressionReport:
        """Run regression analysis across all shared metrics."""
        start = time.monotonic()
        shared_metrics = sorted(set(self._baseline) & set(self._current))
        alerts: list[RegressionAlert] = []

        log = logger.bind(
            metrics=shared_metrics,
            baseline_run=self._baseline_run_id,
            current_run=self._current_run_id,
        )
        log.info("regression_detector.started")

        for metric in shared_metrics:
            baseline = self._baseline[metric]
            current = self._current[metric]
            alert = self._check_metric(metric, baseline, current)
            if alert is not None:
                alerts.append(alert)

        critical_count = sum(1 for a in alerts if a.severity == Severity.CRITICAL)
        warning_count = sum(1 for a in alerts if a.severity == Severity.WARNING)
        has_regression = critical_count > 0 or warning_count > 0

        duration = time.monotonic() - start
        baseline_size = (
            int(np.mean([len(v) for v in self._baseline.values()]))
            if self._baseline else 0
        )
        current_size = (
            int(np.mean([len(v) for v in self._current.values()]))
            if self._current else 0
        )
        report = RegressionReport(
            baseline_sample_size=baseline_size,
            current_sample_size=current_size,
            baseline_run_id=self._baseline_run_id,
            current_run_id=self._current_run_id,
            alerts=alerts,
            metrics_checked=shared_metrics,
            has_regression=has_regression,
            critical_count=critical_count,
            warning_count=warning_count,
            analysis_duration_seconds=duration,
            metadata=self._metadata,
        )

        log.info(
            "regression_detector.completed",
            has_regression=has_regression,
            critical=critical_count,
            warning=warning_count,
            metrics_checked=len(shared_metrics),
        )

        return report

    def _check_metric(
        self,
        metric_name: str,
        baseline: np.ndarray,
        current: np.ndarray,
    ) -> RegressionAlert | None:
        """Check a single metric for regression."""
        mean_b = float(np.mean(baseline))
        mean_c = float(np.mean(current))
        std_b = float(np.std(baseline, ddof=1)) if len(baseline) > 1 else 0.0
        std_c = float(np.std(current, ddof=1)) if len(current) > 1 else 0.0

        # Try scipy first, fall back to simple implementation
        try:
            import scipy.stats  # noqa: F401

            z_score, p_value = z_test_two_sample(baseline, current)
        except ImportError:
            z_score, p_value = simple_t_test(baseline, current)

        absolute_drop = mean_b - mean_c
        relative_drop_pct = (absolute_drop / abs(mean_b) * 100) if mean_b != 0 else 0.0

        is_regression = (
            z_score > self._z_threshold
            and p_value < self._p_threshold
            and relative_drop_pct > self._rel_drop_threshold
        )

        if not is_regression:
            return None

        severity = _classify_severity(p_value, relative_drop_pct)
        msg = (
            f"Regression detected in '{metric_name}': "
            f"baseline={mean_b:.4f} (±{std_b:.4f}), "
            f"current={mean_c:.4f} (±{std_c:.4f}), "
            f"drop={absolute_drop:.4f} ({relative_drop_pct:.1f}%), "
            f"z={z_score:.2f}, p={p_value:.4f}, severity={severity.value}"
        )

        logger.warning(
            "regression_detected",
            metric=metric_name,
            baseline_mean=mean_b,
            current_mean=mean_c,
            relative_drop_pct=round(relative_drop_pct, 2),
            z_score=round(z_score, 4),
            p_value=round(p_value, 4),
            severity=severity.value,
        )

        return RegressionAlert(
            metric_name=metric_name,
            baseline_mean=mean_b,
            baseline_std=std_b,
            current_mean=mean_c,
            current_std=std_c,
            z_score=z_score,
            p_value=p_value,
            absolute_drop=absolute_drop,
            relative_drop_pct=relative_drop_pct,
            severity=severity,
            message=msg,
        )
