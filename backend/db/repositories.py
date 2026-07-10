"""Repository layer — async in-memory stores with clean interfaces.

Replace with real database-backed repositories when ready.
All repositories use an async interface so callers don't need to change
when the backing store switches to a real database.
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


# ---------------------------------------------------------------------------
# Sweep repository
# ---------------------------------------------------------------------------


class SweepRepository:
    """Async in-memory store for optimization sweeps."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        sweep_id = record["sweep_id"]
        self._store[sweep_id] = record
        logger.debug("sweep_repo.create", sweep_id=sweep_id)
        return record

    async def get(self, sweep_id: str) -> dict[str, Any] | None:
        return self._store.get(sweep_id)

    async def update(
        self, sweep_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = self._store.get(sweep_id)
        if record is None:
            return None
        record.update(updates)
        return record
