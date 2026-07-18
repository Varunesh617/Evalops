"""MongoDB mirror for the in-memory repositories.

When ``MONGODB_URL`` is configured, every write to an in-memory repository is
also mirrored (best-effort, fire-and-forget) to MongoDB.  The in-memory store
remains the source of truth for reads/queries used by the dashboard; Mongo is a
durable, queryable log that survives restarts.

Design choices requested by the user:
    * MongoDB (document store) as the durable mirror.
    * A real, monotonically increasing **serial number** per collection
      (``serial_no``) — emulated via an atomic ``find_one_and_update`` on a
      ``__counters`` document, since Mongo has no native auto-increment.
    * Explicit ``created_at`` / ``updated_at`` timestamp columns stored as
      BSON ``datetime`` (ISODate), distinct from the Postgres/SQL models.

Usage::

    mirror = MongoMirror()
    await mirror.start()                 # build client + ensure indexes
    await mirror.write("pipelines", record)
    await mirror.stop()
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Collections we mirror. Keyed by repository name.
COLLECTIONS: dict[str, str] = {
    "pipeline": "pipeline_records",
    "trace": "trace_records",
    "eval": "eval_records",
    "sweep": "sweep_records",
}

_COUNTER_DOC_ID = "__serial_counter__"


class MongoMirror:
    """Best-effort durable mirror of in-memory repository writes to MongoDB."""

    def __init__(self, url: str | None = None, db_name: str = "evalops") -> None:
        self._url = url or os.environ.get("MONGODB_URL", "")
        self._db_name = db_name
        self._client: Any | None = None
        self._db: Any | None = None
        self._started = False

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> bool:
        """Connect to Mongo and ensure indexes. Returns False if unavailable."""
        if not self._url:
            logger.warning("mongo_mirror.disabled", reason="MONGODB_URL not set")
            return False
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            self._client = AsyncIOMotorClient(self._url, serverSelectionTimeoutMS=2000)
            self._db = self._client[self._db_name]
            # Validate connectivity without blocking the app startup.
            await self._client.admin.command("ping")
            # Indexes for common query/filter patterns.
            for coll in COLLECTIONS.values():
                await self._db[coll].create_index([("serial_no", 1)], unique=True)
                await self._db[coll].create_index([("status", 1)])
                await self._db[coll].create_index([("created_at", -1)])
            self._started = True
            logger.info("mongo_mirror.started", db=self._db_name)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo_mirror.unavailable", error=str(exc))
            self._client = None
            self._db = None
            self._started = False
            return False

    async def stop(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._db = None
        self._started = False

    @property
    def is_active(self) -> bool:
        return self._started and self._db is not None

    # -- serial number emulation -------------------------------------------

    async def _next_serial(self, collection: str) -> int:
        """Atomically allocate the next serial number for a collection."""
        assert self._db is not None
        counters = self._db["__counters"]
        result = await counters.find_one_and_update(
            {"_id": f"{_COUNTER_DOC_ID}:{collection}"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True,
        )
        return int(result["seq"])

    # -- writes -------------------------------------------------------------

    async def write(self, repo_name: str, record: dict[str, Any]) -> None:
        """Mirror a create/update of ``record`` into the appropriate collection.

        Safe to call from anywhere: if Mongo is unavailable it logs and returns
        without raising, so the primary in-memory path is never blocked.
        """
        if not self.is_active:
            return
        coll_name = COLLECTIONS.get(repo_name)
        if coll_name is None:
            return
        try:
            now = datetime.now(UTC)
            doc = dict(record)
            doc["mirrored_at"] = now

            existing = await self._db[coll_name].find_one(  # type: ignore[union-attr]
                {"id": doc.get("id")}
            )
            if existing is None:
                doc["serial_no"] = await self._next_serial(coll_name)
                doc["created_at"] = now
                doc["updated_at"] = now
                await self._db[coll_name].insert_one(doc)
            else:
                doc["serial_no"] = existing.get("serial_no")
                doc["created_at"] = existing.get("created_at", now)
                doc["updated_at"] = now
                await self._db[coll_name].replace_one({"_id": existing["_id"]}, doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mongo_mirror.write_failed", repo=repo_name, error=str(exc)
            )

    async def delete(self, repo_name: str, record_id: str) -> None:
        if not self.is_active:
            return
        coll_name = COLLECTIONS.get(repo_name)
        if coll_name is None:
            return
        try:
            await self._db[coll_name].delete_one({"id": record_id})  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo_mirror.delete_failed", repo=repo_name, error=str(exc))


# Module-level singleton used by the repository wrappers.
_mirror: MongoMirror | None = None


def get_mirror() -> MongoMirror:
    """Return the process-wide MongoMirror singleton (lazy)."""
    global _mirror
    if _mirror is None:
        _mirror = MongoMirror()
    return _mirror
