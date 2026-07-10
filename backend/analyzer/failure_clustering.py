"""Failure clustering for evaluation failures.

Clusters similar failure patterns, identifies common root causes,
and groups failures by trajectory similarity.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from typing import Any

import numpy as np
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FailureRecord(BaseModel):
    """A single evaluation failure."""

    failure_id: str
    step_name: str
    step_index: int = Field(ge=0)
    error_type: str
    error_message: str
    trajectory: list[str] = Field(default_factory=list)
    input_text: str = ""
    expected_output: str = ""
    actual_output: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FailureCluster(BaseModel):
    """A cluster of similar failures."""

    cluster_id: int
    size: int = Field(ge=1)
    representative_failure_id: str
    common_step: str
    common_error_type: str
    root_cause_hypothesis: str
    trajectory_signature: str
    failure_ids: list[str] = Field(default_factory=list)
    avg_score: float = Field(default=0.0)
    severity: str = "medium"


class ClusteringResult(BaseModel):
    """Full result of failure clustering analysis."""

    clusters: list[FailureCluster] = Field(default_factory=list)
    total_failures: int = 0
    clustered_failures: int = 0
    unclustered_count: int = 0
    n_clusters: int = 0
    dominant_error_types: list[tuple[str, int]] = Field(default_factory=list)
    dominant_steps: list[tuple[str, int]] = Field(default_factory=list)
    clustering_duration_seconds: float = 0.0
    similarity_threshold: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _error_type_vector(error_type: str, all_error_types: list[str]) -> np.ndarray:
    """One-hot encode error type."""
    vec = np.zeros(len(all_error_types), dtype=np.float64)
    if error_type in all_error_types:
        vec[all_error_types.index(error_type)] = 1.0
    return vec


def _step_vector(step_name: str, all_steps: list[str]) -> np.ndarray:
    """One-hot encode step name."""
    vec = np.zeros(len(all_steps), dtype=np.float64)
    if step_name in all_steps:
        vec[all_steps.index(step_name)] = 1.0
    return vec


def _trajectory_signature(trajectory: list[str]) -> str:
    """Hash the trajectory path for grouping."""
    path = " -> ".join(trajectory)
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def _trajectory_vector(trajectory: list[str], all_steps: list[str]) -> np.ndarray:
    """Create a trajectory feature vector: counts + presence of each step."""
    vec = np.zeros(len(all_steps), dtype=np.float64)
    counts = Counter(trajectory)
    for i, step in enumerate(all_steps):
        vec[i] = counts.get(step, 0)
    # Normalise
    total = sum(counts.values()) if counts else 1
    vec /= total
    return vec


def _text_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity on character n-grams."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    ngram_size = 3
    set_a = {a[i : i + ngram_size] for i in range(max(0, len(a) - ngram_size + 1))}
    set_b = {b[i : i + ngram_size] for i in range(max(0, len(b) - ngram_size + 1))}
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def extract_features(
    failures: list[FailureRecord],
) -> np.ndarray:
    """Extract a feature matrix from failure records.

    Features: error_type (one-hot) + step_name (one-hot) + trajectory (normalised counts)
    + error_message similarity features + score.
    """
    if not failures:
        return np.array([]).reshape(0, 1)

    all_error_types = sorted({f.error_type for f in failures})
    step_names = {f.step_name for f in failures}
    traj_steps = {s for f in failures for s in f.trajectory}
    all_steps = sorted(step_names | traj_steps)

    rows: list[np.ndarray] = []
    for f in failures:
        et_vec = _error_type_vector(f.error_type, all_error_types)
        step_vec = _step_vector(f.step_name, all_steps)
        traj_vec = _trajectory_vector(f.trajectory, all_steps)
        score_vec = np.array([f.score], dtype=np.float64)
        rows.append(np.concatenate([et_vec, step_vec, traj_vec, score_vec]))

    return np.vstack(rows)


# ---------------------------------------------------------------------------
# Distance & clustering
# ---------------------------------------------------------------------------


def _euclidean_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Euclidean distance between two feature vectors."""
    return float(np.sqrt(np.sum((a - b) ** 2)))


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance (1 - cosine_similarity)."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - float(np.dot(a, b) / (norm_a * norm_b))


def _distance_matrix(features: np.ndarray) -> np.ndarray:
    """Compute pairwise distance matrix."""
    n = features.shape[0]
    dist = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            d = _cosine_distance(features[i], features[j])
            dist[i, j] = d
            dist[j, i] = d
    return dist


def _agglomerative_clustering(
    dist_matrix: np.ndarray,
    threshold: float,
) -> list[list[int]]:
    """Single-linkage agglomerative clustering.

    Returns list of clusters (each cluster is a list of indices).
    """
    n = dist_matrix.shape[0]
    if n == 0:
        return []

    # Initialise: each point in its own cluster
    clusters: list[set[int]] = [{i} for i in range(n)]
    # Merge history not needed; just use threshold-based approach

    # Simple single-linkage merging
    active = list(range(len(clusters)))
    while len(active) > 1:
        best_dist = float("inf")
        best_pair = (-1, -1)
        for i_idx in range(len(active)):
            for j_idx in range(i_idx + 1, len(active)):
                ci = active[i_idx]
                cj = active[j_idx]
                # Single linkage: min distance between clusters
                min_d = min(dist_matrix[a, b] for a in clusters[ci] for b in clusters[cj])
                if min_d < best_dist:
                    best_dist = min_d
                    best_pair = (i_idx, j_idx)
        if best_dist > threshold:
            break
        # Merge
        i_idx, j_idx = best_pair
        ci, cj = active[i_idx], active[j_idx]
        clusters[ci] = clusters[ci] | clusters[cj]
        active.pop(j_idx)

    return [sorted(clusters[i]) for i in active]


# ---------------------------------------------------------------------------
# Root cause analysis
# ---------------------------------------------------------------------------


def _hypothesize_root_cause(
    group: list[FailureRecord],
) -> str:
    """Generate a root cause hypothesis string from a group of failures."""
    error_types = Counter(f.error_type for f in group)
    steps = Counter(f.step_name for f in group)
    messages = [f.error_message for f in group]

    dominant_error = error_types.most_common(1)[0][0] if error_types else "unknown"
    dominant_step = steps.most_common(1)[0][0] if steps else "unknown"

    # Look for common substrings in error messages
    common_words: Counter[str] = Counter()
    for msg in messages:
        words = msg.lower().split()
        common_words.update(words)
    most_common = [w for w, _ in common_words.most_common(5) if _ > 1]

    parts = [f"Failures cluster around '{dominant_step}' step with '{dominant_error}' errors"]
    if most_common:
        parts.append(f"common terms: {', '.join(most_common)}")
    avg_score = np.mean([f.score for f in group]) if group else 0
    parts.append(f"avg score: {avg_score:.3f}")

    return ". ".join(parts)


def _classify_severity(group: list[FailureRecord]) -> str:
    """Classify cluster severity."""
    avg_score = float(np.mean([f.score for f in group])) if group else 0.0
    size = len(group)
    if avg_score < 0.2 and size >= 3:
        return "critical"
    if avg_score < 0.4 or size >= 5:
        return "high"
    if avg_score < 0.6:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main clustering
# ---------------------------------------------------------------------------


class FailureClustering:
    """Cluster evaluation failures and identify common root causes.

    Usage::

        clustering = FailureClustering(failures=failure_records)
        result = clustering.cluster()
        for cluster in result.clusters:
            print(cluster.root_cause_hypothesis)
    """

    def __init__(
        self,
        failures: list[FailureRecord],
        *,
        similarity_threshold: float = 0.6,
        min_cluster_size: int = 2,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._failures = failures
        self._threshold = 1.0 - similarity_threshold  # convert similarity to distance
        self._min_cluster_size = min_cluster_size
        self._metadata = metadata or {}

    def cluster(self) -> ClusteringResult:
        """Run clustering and return results."""
        start = time.monotonic()
        log = logger.bind(n_failures=len(self._failures), threshold=self._threshold)
        log.info("failure_clustering.started")

        if not self._failures:
            return ClusteringResult(
                clusters=[],
                total_failures=0,
                clustering_duration_seconds=time.monotonic() - start,
                similarity_threshold=self._threshold,
            )

        features = extract_features(self._failures)
        if features.shape[0] == 0:
            return ClusteringResult(
                clusters=[],
                total_failures=len(self._failures),
                clustering_duration_seconds=time.monotonic() - start,
                similarity_threshold=self._threshold,
            )

        dist_matrix = _distance_matrix(features)
        raw_clusters = _agglomerative_clustering(dist_matrix, self._threshold)

        # Filter small clusters
        valid_clusters = [c for c in raw_clusters if len(c) >= self._min_cluster_size]
        # Collect clustered indices
        clustered_indices = {idx for cluster in valid_clusters for idx in cluster}

        # Build FailureCluster objects
        clusters: list[FailureCluster] = []
        for cid, indices in enumerate(valid_clusters):
            group = [self._failures[i] for i in indices]
            representative = max(group, key=lambda f: f.score)
            trajectory_sig = _trajectory_signature(representative.trajectory)
            hypothesis = _hypothesize_root_cause(group)
            severity = _classify_severity(group)
            avg_score = float(np.mean([f.score for f in group]))

            clusters.append(
                FailureCluster(
                    cluster_id=cid,
                    size=len(group),
                    representative_failure_id=representative.failure_id,
                    common_step=representative.step_name,
                    common_error_type=representative.error_type,
                    root_cause_hypothesis=hypothesis,
                    trajectory_signature=trajectory_sig,
                    failure_ids=[f.failure_id for f in group],
                    avg_score=avg_score,
                    severity=severity,
                )
            )

        # Compute summary stats
        all_errors = [f.error_type for f in self._failures]
        all_steps_list = [f.step_name for f in self._failures]
        dominant_errors = Counter(all_errors).most_common(5)
        dominant_steps = Counter(all_steps_list).most_common(5)

        duration = time.monotonic() - start
        result = ClusteringResult(
            clusters=clusters,
            total_failures=len(self._failures),
            clustered_failures=len(clustered_indices),
            unclustered_count=len(self._failures) - len(clustered_indices),
            n_clusters=len(clusters),
            dominant_error_types=dominant_errors,
            dominant_steps=dominant_steps,
            clustering_duration_seconds=duration,
            similarity_threshold=self._threshold,
            metadata=self._metadata,
        )

        log.info(
            "failure_clustering.completed",
            n_clusters=len(clusters),
            clustered=len(clustered_indices),
            unclustered=result.unclustered_count,
            total_seconds=round(duration, 3),
        )

        return result
