"""Repository layer — async in-memory stores with clean interfaces.

Used as a fallback when no DATABASE_URL is configured.  All repositories use an
async interface so callers don't need to change when the backing store switches
to a real database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pipeline repository
# ---------------------------------------------------------------------------


class PipelineRepository:
    """Async in-memory store for pipeline configurations."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        pipeline_id = record["id"]
        self._store[pipeline_id] = record
        logger.debug("pipeline_repo.create", pipeline_id=pipeline_id)
        return record

    async def get(self, pipeline_id: str) -> dict[str, Any] | None:
        return self._store.get(pipeline_id)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        for record in self._store.values():
            if record.get("name") == name:
                return record
        return None

    async def list(
        self,
        *,
        status: str | None = None,
        tag: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        items = list(self._store.values())
        if status is not None:
            items = [p for p in items if p["status"] == status]
        if tag is not None:
            items = [p for p in items if tag in p.get("tags", [])]
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    async def update(
        self, pipeline_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = self._store.get(pipeline_id)
        if record is None:
            return None
        record.update(updates)
        return record

    async def delete(self, pipeline_id: str) -> bool:
        if pipeline_id not in self._store:
            return False
        del self._store[pipeline_id]
        return True

    async def count(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Trace repository
# ---------------------------------------------------------------------------


class TraceRepository:
    """Async in-memory store for execution traces."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        trace_id = record["id"]
        self._store[trace_id] = record
        logger.debug("trace_repo.create", trace_id=trace_id)
        return record

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        return self._store.get(trace_id)

    async def get_with_steps(self, trace_id: str) -> dict[str, Any] | None:
        trace = self._store.get(trace_id)
        if trace is None:
            return None
        trace["trace_steps"] = trace.get("trace_steps", [])
        return trace

    async def list(
        self,
        *,
        pipeline_id: str | None = None,
        status: str | None = None,
        min_cost: float | None = None,
        max_cost: float | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        items = list(self._store.values())
        if pipeline_id is not None:
            items = [t for t in items if t.get("pipeline_id") == pipeline_id]
        if status is not None:
            items = [t for t in items if t.get("status") == status]
        if min_cost is not None:
            items = [t for t in items if t.get("total_cost_usd", 0) >= min_cost]
        if max_cost is not None:
            items = [t for t in items if t.get("total_cost_usd", 0) <= max_cost]
        items.sort(key=lambda t: t.get("started_at", datetime.min), reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    async def update(
        self, trace_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = self._store.get(trace_id)
        if record is None:
            return None
        record.update(updates)
        return record

    async def delete(self, trace_id: str) -> bool:
        if trace_id not in self._store:
            return False
        del self._store[trace_id]
        return True

    async def count(self) -> int:
        return len(self._store)

    async def count_by_pipeline(self, pipeline_id: str) -> int:
        return sum(
            1 for t in self._store.values() if t.get("pipeline_id") == pipeline_id
        )


# ---------------------------------------------------------------------------
# Eval repository
# ---------------------------------------------------------------------------


class EvalRepository:
    """Async in-memory store for evaluation results."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        eval_id = record["id"]
        self._store[eval_id] = record
        logger.debug("eval_repo.create", eval_id=eval_id)
        return record

    async def get(self, eval_id: str) -> dict[str, Any] | None:
        return self._store.get(eval_id)

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        items = list(self._store.values())
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    async def update(
        self, eval_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = self._store.get(eval_id)
        if record is None:
            return None
        record.update(updates)
        return record

    async def delete(self, eval_id: str) -> bool:
        if eval_id not in self._store:
            return False
        del self._store[eval_id]
        return True

    async def count(self) -> int:
        return len(self._store)

    async def list_by_trajectory(
        self, trajectory_id: str, *, offset: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        items = [
            r for r in self._store.values() if r.get("trajectory_id") == trajectory_id
        ]
        items.sort(key=lambda r: r.get("created_at", datetime.min), reverse=True)
        return items[offset : offset + limit]

    async def get_best_for_trajectory(
        self, trajectory_id: str
    ) -> dict[str, Any] | None:
        candidates = [
            r for r in self._store.values() if r.get("trajectory_id") == trajectory_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.get("aggregate_score", 0.0))

    async def list_high_scores(
        self, min_score: float, *, offset: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        items = [
            r for r in self._store.values() if r.get("aggregate_score", 0.0) >= min_score
        ]
        items.sort(key=lambda r: r.get("aggregate_score", 0.0), reverse=True)
        return items[offset : offset + limit]


# ---------------------------------------------------------------------------
# Sweep repository
# ---------------------------------------------------------------------------


class SweepRepository:
    """Async in-memory store for optimization sweeps.

    Uses ``id`` as the store key (matching SQLAlchemy ``BaseRepository``) and
    transparently maps ``sweep_id`` on reads so callers see ``sweep_id`` in
    returned dicts.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._trials: dict[str, list[dict[str, Any]]] = {}

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        mapped = dict(record)
        if "sweep_id" in mapped:
            mapped["id"] = mapped.pop("sweep_id")
        sweep_id = mapped["id"]
        self._store[sweep_id] = mapped
        mapped["sweep_id"] = sweep_id
        logger.debug("sweep_repo.create", sweep_id=sweep_id)
        return mapped

    async def get(self, sweep_id: str) -> dict[str, Any] | None:
        record = self._store.get(sweep_id)
        if record is None:
            return None
        record["sweep_id"] = record["id"]
        return record

    async def update(
        self, sweep_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = self._store.get(sweep_id)
        if record is None:
            return None
        record.update(updates)
        record["sweep_id"] = record["id"]
        return record

    async def delete(self, sweep_id: str) -> bool:
        if sweep_id not in self._store:
            return False
        del self._store[sweep_id]
        self._trials.pop(sweep_id, None)
        return True

    async def count(self) -> int:
        return len(self._store)

    async def get_active(self, pipeline_id: str) -> dict[str, Any] | None:
        for record in self._store.values():
            if record.get("pipeline_id") == pipeline_id and record.get("status") == "running":
                record["sweep_id"] = record["id"]
                return record
        return None

    async def add_trial(self, sweep_id: str, **kwargs: Any) -> dict[str, Any]:
        trials = self._trials.setdefault(sweep_id, [])
        trial_number = len(trials) + 1
        trial = {"sweep_id": sweep_id, "trial_number": trial_number, **kwargs}
        trials.append(trial)
        return trial

    async def list_trials(
        self, sweep_id: str, *, offset: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        trials = self._trials.get(sweep_id, [])
        return trials[offset : offset + limit]
