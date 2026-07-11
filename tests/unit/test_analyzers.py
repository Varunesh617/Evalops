"""Tests for regression detector and failure clustering."""

from __future__ import annotations

import numpy as np
import pytest

from backend.analyzer.failure_clustering import (
    ClusteringResult,
    FailureCluster,
    FailureClustering,
    FailureRecord,
    _cosine_distance,
    _euclidean_distance,
    _text_similarity,
    _trajectory_signature,
    extract_features,
)
from backend.analyzer.regression_detector import (
    RegressionAlert,
    RegressionDetector,
    RegressionReport,
    Severity,
    _classify_severity,
    _erf,
    simple_t_test,
    z_test_two_sample,
)


# ---------------------------------------------------------------------------
# Regression detector statistical tests
# ---------------------------------------------------------------------------


class TestStatisticalHelpers:
    def test_z_test_identical(self):
        a = np.array([0.8, 0.85, 0.9, 0.82, 0.88])
        b = np.array([0.8, 0.85, 0.9, 0.82, 0.88])
        z, p = z_test_two_sample(a, b)
        assert z == pytest.approx(0.0, abs=0.1)
        assert p > 0.05

    def test_z_test_significant_drop(self):
        baseline = np.array([0.9, 0.92, 0.88, 0.91, 0.89, 0.93, 0.87, 0.90, 0.91, 0.88])
        current = np.array([0.7, 0.72, 0.68, 0.71, 0.69, 0.73, 0.67, 0.70, 0.71, 0.68])
        z, p = z_test_two_sample(baseline, current)
        assert z > 1.645
        assert p < 0.05

    def test_z_test_small_sample(self):
        a = np.array([0.8])
        b = np.array([0.9])
        z, p = z_test_two_sample(a, b)
        assert z == 0.0
        assert p == 1.0

    def test_simple_t_test_identical(self):
        a = np.array([0.8, 0.85, 0.9])
        b = np.array([0.8, 0.85, 0.9])
        z, p = simple_t_test(a, b)
        assert z == pytest.approx(0.0, abs=0.1)

    def test_simple_t_test_drop(self):
        baseline = np.array([0.9, 0.92, 0.88, 0.91, 0.89])
        current = np.array([0.7, 0.72, 0.68, 0.71, 0.69])
        z, p = simple_t_test(baseline, current)
        assert z > 0

    def test_simple_t_test_small_sample(self):
        z, p = simple_t_test(np.array([0.5]), np.array([0.5]))
        assert z == 0.0
        assert p == 1.0

    def test_erf(self):
        assert _erf(0.0) == pytest.approx(0.0, abs=0.01)
        assert _erf(1.0) > 0.0
        assert _erf(-1.0) < 0.0
        assert _erf(100.0) == pytest.approx(1.0, abs=0.01)

    def test_classify_severity_critical(self):
        sev = _classify_severity(0.005, 15.0)
        assert sev == Severity.CRITICAL

    def test_classify_severity_warning(self):
        sev = _classify_severity(0.03, 5.0)
        assert sev == Severity.WARNING

    def test_classify_severity_info(self):
        sev = _classify_severity(0.1, 3.0)
        assert sev == Severity.INFO


# ---------------------------------------------------------------------------
# RegressionDetector tests
# ---------------------------------------------------------------------------


class TestRegressionDetector:
    def test_no_regression(self):
        baseline = {m: np.array([0.8, 0.85, 0.9, 0.82]) for m in ["faithfulness"]}
        current = {m: np.array([0.81, 0.84, 0.89, 0.83]) for m in ["faithfulness"]}
        detector = RegressionDetector(baseline, current)
        report = detector.analyze()
        assert report.has_regression is False
        assert len(report.alerts) == 0

    def test_detects_regression(self):
        np.random.seed(42)
        baseline = {"faithfulness": np.random.normal(0.85, 0.02, 30)}
        current = {"faithfulness": np.random.normal(0.70, 0.03, 30)}
        detector = RegressionDetector(
            baseline, current,
            z_threshold=1.645,
            p_threshold=0.05,
            relative_drop_threshold_pct=5.0,
        )
        report = detector.analyze()
        assert report.has_regression is True
        assert len(report.alerts) >= 1
        assert report.alerts[0].metric_name == "faithfulness"
        assert report.alerts[0].severity in (Severity.WARNING, Severity.CRITICAL)

    def test_empty_metrics(self):
        detector = RegressionDetector({}, {})
        report = detector.analyze()
        assert report.has_regression is False

    def test_partial_metrics(self):
        baseline = {"faithfulness": np.array([0.8, 0.85, 0.9])}
        current = {"faithfulness": np.array([0.8, 0.85, 0.9]), "relevance": np.array([0.5])}
        detector = RegressionDetector(baseline, current)
        report = detector.analyze()
        assert "faithfulness" in report.metrics_checked

    def test_report_fields(self):
        detector = RegressionDetector(
            {"m": np.array([0.8, 0.9])},
            {"m": np.array([0.8, 0.9])},
            baseline_run_id="b1",
            current_run_id="c1",
        )
        report = detector.analyze()
        assert report.baseline_run_id == "b1"
        assert report.current_run_id == "c1"
        assert report.baseline_sample_size == 2

    def test_alert_to_dict(self):
        alert = RegressionAlert(
            metric_name="test",
            baseline_mean=0.8,
            baseline_std=0.05,
            current_mean=0.6,
            current_std=0.1,
            z_score=3.0,
            p_value=0.001,
            absolute_drop=0.2,
            relative_drop_pct=25.0,
            severity=Severity.CRITICAL,
            message="test regression",
        )
        d = alert.model_dump()
        assert d["metric_name"] == "test"
        assert d["severity"] == "critical"


# ---------------------------------------------------------------------------
# Failure clustering tests
# ---------------------------------------------------------------------------


class TestFailureClusteringHelpers:
    def test_text_similarity_identical(self):
        assert _text_similarity("hello world", "hello world") == 1.0

    def test_text_similarity_empty(self):
        assert _text_similarity("", "") == 1.0

    def test_text_similarity_one_empty(self):
        assert _text_similarity("hello", "") == 0.0

    def test_text_similarity_similar(self):
        sim = _text_similarity("hello world", "hello worlds")
        assert sim > 0.5

    def test_text_similarity_different(self):
        sim = _text_similarity("abcdef", "xyz123")
        assert sim < 0.5

    def test_trajectory_signature(self):
        sig1 = _trajectory_signature(["a", "b", "c"])
        sig2 = _trajectory_signature(["a", "b", "c"])
        assert sig1 == sig2

    def test_trajectory_signature_different(self):
        sig1 = _trajectory_signature(["a", "b"])
        sig2 = _trajectory_signature(["b", "a"])
        assert sig1 != sig2

    def test_euclidean_distance(self):
        a = np.array([0.0, 0.0])
        b = np.array([3.0, 4.0])
        assert _euclidean_distance(a, b) == pytest.approx(5.0)

    def test_cosine_distance_identical(self):
        a = np.array([1.0, 0.0])
        assert _cosine_distance(a, a) == pytest.approx(0.0)

    def test_cosine_distance_orthogonal(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert _cosine_distance(a, b) == pytest.approx(1.0)

    def test_cosine_distance_zero_vector(self):
        a = np.array([0.0, 0.0])
        b = np.array([1.0, 0.0])
        assert _cosine_distance(a, b) == 1.0


class TestExtractFeatures:
    def test_empty(self):
        features = extract_features([])
        assert features.shape == (0, 1)

    def test_single_failure(self):
        f = FailureRecord(
            failure_id="f1", step_name="retrieve", step_index=0,
            error_type="timeout", error_message="timed out",
            trajectory=["retrieve", "rerank"], score=0.3,
        )
        features = extract_features([f])
        assert features.shape[0] == 1
        assert features.shape[1] > 0

    def test_multiple_failures(self):
        failures = [
            FailureRecord(
                failure_id=f"f{i}", step_name="retrieve" if i < 3 else "reason",
                step_index=0, error_type="timeout" if i < 3 else "value_error",
                error_message="err", trajectory=["retrieve"], score=0.3,
            )
            for i in range(5)
        ]
        features = extract_features(failures)
        assert features.shape[0] == 5


class TestFailureClustering:
    def _make_failures(self, n=10, same_type=True):
        return [
            FailureRecord(
                failure_id=f"f{i}",
                step_name="retrieve" if i < n // 2 else "reason",
                step_index=0,
                error_type="timeout" if same_type else ("timeout" if i < n // 2 else "value_error"),
                error_message=f"error {i}" if not same_type else "same error message timeout error",
                trajectory=["retrieve", "rerank"] if i < n // 2 else ["reason", "generate"],
                score=0.1 + (i * 0.05),
            )
            for i in range(n)
        ]

    def test_empty_failures(self):
        clustering = FailureClustering(failures=[])
        result = clustering.cluster()
        assert result.total_failures == 0
        assert result.n_clusters == 0

    def test_clusters_similar_failures(self):
        failures = self._make_failures(n=10, same_type=True)
        clustering = FailureClustering(failures=failures, similarity_threshold=0.6, min_cluster_size=2)
        result = clustering.cluster()
        assert result.total_failures == 10
        assert result.n_clusters >= 1

    def test_no_clusters_high_threshold(self):
        failures = self._make_failures(n=3, same_type=False)
        # Very strict threshold -> no clustering
        clustering = FailureClustering(failures=failures, similarity_threshold=0.99, min_cluster_size=10)
        result = clustering.cluster()
        assert result.n_clusters == 0

    def test_cluster_fields(self):
        failures = self._make_failures(n=6, same_type=True)
        clustering = FailureClustering(failures=failures, similarity_threshold=0.4, min_cluster_size=2)
        result = clustering.cluster()
        if result.clusters:
            c = result.clusters[0]
            assert isinstance(c, FailureCluster)
            assert c.size >= 2
            assert len(c.failure_ids) >= 2
            assert c.root_cause_hypothesis != ""
            assert c.severity in ("low", "medium", "high", "critical")

    def test_dominant_error_types(self):
        failures = self._make_failures(n=6, same_type=True)
        clustering = FailureClustering(failures=failures)
        result = clustering.cluster()
        assert len(result.dominant_error_types) > 0

    def test_clustered_vs_unclustered(self):
        failures = self._make_failures(n=6, same_type=True)
        clustering = FailureClustering(failures=failures, similarity_threshold=0.4, min_cluster_size=2)
        result = clustering.cluster()
        assert result.clustered_failures + result.unclustered_count == result.total_failures
