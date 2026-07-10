"""Auto-deployment pipeline with configurable policies.

Evaluates optimization candidates against deployment policies, applies
configurations to pipelines, and supports rollback to previous configs.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from backend.core.config import PipelineConfig

logger = structlog.get_logger(__name__)

_DEFAULT_DEPLOY_DIR = Path("data/deployments")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeployPolicy(BaseModel):
    """Configurable rules that control auto-deployment."""

    min_quality_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_cost_threshold: float = Field(default=5.0, ge=0.0)
    min_trials_before_deploy: int = Field(default=5, ge=0)
    require_all_tests_pass: bool = True
    enabled: bool = True


class DeployResult(BaseModel):
    """Outcome of a deploy or rollback operation."""

    deployment_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    pipeline_id: str
    status: str  # "deployed", "rollback", "rejected", "failed"
    config_diff: dict[str, Any] = Field(default_factory=dict)
    previous_config: dict[str, Any] = Field(default_factory=dict)
    new_config: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    rollback_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeploymentRecord(BaseModel):
    """A single deployment in history."""

    deployment_id: str
    pipeline_id: str
    status: str
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    previous_config: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# DeployManager
# ---------------------------------------------------------------------------


class DeployManager:
    """Orchestrate config deployment with policy enforcement and rollback.

    Usage::

        manager = DeployManager()
        result = manager.evaluate_deployment_candidate(
            pipeline_id="p-1",
            config=new_config,
            quality_score=0.85,
            cost_usd=1.2,
            trials_completed=10,
        )
        if result.status == "approved":
            deploy_result = manager.deploy_config("p-1", new_config)
    """

    def __init__(
        self,
        policy: DeployPolicy | None = None,
        deploy_dir: Path | str = _DEFAULT_DEPLOY_DIR,
    ) -> None:
        self._policy = policy or DeployPolicy()
        self._deploy_dir = Path(deploy_dir)
        self._deploy_dir.mkdir(parents=True, exist_ok=True)

    @property
    def policy(self) -> DeployPolicy:
        return self._policy

    @policy.setter
    def policy(self, value: DeployPolicy) -> None:
        self._policy = value

    # -- Evaluation ---------------------------------------------------------

    def evaluate_deployment_candidate(
        self,
        *,
        pipeline_id: str,
        config: PipelineConfig,
        quality_score: float,
        cost_usd: float,
        latency_ms: float = 0.0,
        trials_completed: int = 0,
        tests_passed: bool = True,
    ) -> DeployResult:
        """Check if a config candidate meets the deployment policy."""
        violations: list[str] = []

        if not self._policy.enabled:
            return DeployResult(
                pipeline_id=pipeline_id,
                status="rejected",
                message="Deployment policy is disabled",
            )

        if quality_score < self._policy.min_quality_threshold:
            violations.append(
                f"quality {quality_score:.4f} < threshold {self._policy.min_quality_threshold}"
            )
        if cost_usd > self._policy.max_cost_threshold:
            violations.append(
                f"cost ${cost_usd:.4f} > threshold ${self._policy.max_cost_threshold}"
            )
        if trials_completed < self._policy.min_trials_before_deploy:
            violations.append(
                f"trials {trials_completed} < minimum {self._policy.min_trials_before_deploy}"
            )
        if self._policy.require_all_tests_pass and not tests_passed:
            violations.append("not all tests passed")

        if violations:
            result = DeployResult(
                pipeline_id=pipeline_id,
                status="rejected",
                message="; ".join(violations),
                new_config=config.model_dump(mode="json"),
            )
            logger.info(
                "deploy_manager.candidate_rejected",
                pipeline_id=pipeline_id,
                violations=violations,
            )
            return result

        return DeployResult(
            pipeline_id=pipeline_id,
            status="approved",
            message="Candidate meets all policy requirements",
            new_config=config.model_dump(mode="json"),
        )

    # -- Deployment ---------------------------------------------------------

    def deploy_config(
        self,
        pipeline_id: str,
        config: PipelineConfig,
        *,
        previous_config: PipelineConfig | None = None,
        message: str = "",
    ) -> DeployResult:
        """Apply a config to a pipeline and record the deployment."""
        config_snapshot = config.model_dump(mode="json")
        prev_snapshot = previous_config.model_dump(mode="json") if previous_config else {}

        config_diff = _compute_config_diff(prev_snapshot, config_snapshot)

        # Save previous config for rollback
        if prev_snapshot:
            self._save_snapshot(pipeline_id, prev_snapshot, label="previous")

        self._save_snapshot(pipeline_id, config_snapshot, label="current")
        record = self._record_deployment(
            pipeline_id=pipeline_id,
            status="deployed",
            config_snapshot=config_snapshot,
            previous_config=prev_snapshot,
            message=message or "Config deployed successfully",
        )

        logger.info(
            "deploy_manager.config_deployed",
            deployment_id=record.deployment_id,
            pipeline_id=pipeline_id,
            diff_keys=list(config_diff.keys()),
        )

        return DeployResult(
            deployment_id=record.deployment_id,
            pipeline_id=pipeline_id,
            status="deployed",
            config_diff=config_diff,
            previous_config=prev_snapshot,
            new_config=config_snapshot,
            message=message or "Config deployed successfully",
        )

    def rollback(self, pipeline_id: str) -> DeployResult:
        """Revert to the previous config for a pipeline."""
        previous = self._load_snapshot(pipeline_id, label="previous")
        current = self._load_snapshot(pipeline_id, label="current")

        if previous is None:
            return DeployResult(
                pipeline_id=pipeline_id,
                status="failed",
                message="No previous configuration available for rollback",
            )

        config_diff = _compute_config_diff(current or {}, previous)

        # Swap: current becomes previous, previous becomes current
        self._save_snapshot(pipeline_id, previous, label="current")

        record = self._record_deployment(
            pipeline_id=pipeline_id,
            status="rollback",
            config_snapshot=previous,
            previous_config=current or {},
            message="Rolled back to previous config",
        )

        logger.info(
            "deploy_manager.rollback_completed",
            deployment_id=record.deployment_id,
            pipeline_id=pipeline_id,
        )

        return DeployResult(
            deployment_id=record.deployment_id,
            pipeline_id=pipeline_id,
            status="rollback",
            config_diff=config_diff,
            previous_config=current or {},
            new_config=previous,
            message="Rolled back to previous configuration",
            rollback_id=record.deployment_id,
        )

    def get_deployment_history(
        self, pipeline_id: str, *, limit: int = 20
    ) -> list[DeploymentRecord]:
        """List past deployments for a pipeline, newest first."""
        history = self._load_history(pipeline_id)
        history.sort(key=lambda r: r.created_at, reverse=True)
        return history[:limit]

    # -- Internal storage ---------------------------------------------------

    def _snapshot_path(self, pipeline_id: str, label: str) -> Path:
        return self._deploy_dir / pipeline_id / f"{label}.json"

    def _save_snapshot(self, pipeline_id: str, config: dict[str, Any], *, label: str) -> None:
        path = self._snapshot_path(pipeline_id, label)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")

    def _load_snapshot(self, pipeline_id: str, *, label: str) -> dict[str, Any] | None:
        path = self._snapshot_path(pipeline_id, label)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _history_path(self, pipeline_id: str) -> Path:
        return self._deploy_dir / pipeline_id / "history.json"

    def _record_deployment(
        self,
        *,
        pipeline_id: str,
        status: str,
        config_snapshot: dict[str, Any],
        previous_config: dict[str, Any],
        message: str,
    ) -> DeploymentRecord:
        record = DeploymentRecord(
            deployment_id=uuid.uuid4().hex[:12],
            pipeline_id=pipeline_id,
            status=status,
            config_snapshot=config_snapshot,
            previous_config=previous_config,
            message=message,
        )
        history = self._load_history(pipeline_id)
        history.append(record)

        path = self._history_path(pipeline_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([r.model_dump(mode="json") for r in history], indent=2, default=str),
            encoding="utf-8",
        )
        return record

    def _load_history(self, pipeline_id: str) -> list[DeploymentRecord]:
        path = self._history_path(pipeline_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [DeploymentRecord.model_validate(r) for r in data]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_config_diff(
    old: dict[str, Any], new: dict[str, Any], *, _prefix: str = ""
) -> dict[str, Any]:
    """Compute a nested diff between two config dicts.

    Returns a dict of ``{path: {"old": ..., "new": ...}}`` for changed values.
    """
    diff: dict[str, Any] = {}
    all_keys = set(old) | set(new)

    for key in sorted(all_keys):
        path = f"{_prefix}.{key}" if _prefix else key
        old_val = old.get(key)
        new_val = new.get(key)

        if old_val == new_val:
            continue

        if isinstance(old_val, dict) and isinstance(new_val, dict):
            nested = _compute_config_diff(old_val, new_val, _prefix=path)
            diff.update(nested)
        else:
            diff[path] = {"old": old_val, "new": new_val}

    return diff
