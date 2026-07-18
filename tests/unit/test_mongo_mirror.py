"""Tests for the MongoDB mirror of in-memory repositories.

Uses ``mongomock_motor`` so no real MongoDB server is required. Verifies that
mirrored writes receive a monotonically increasing ``serial_no`` and real
``created_at`` / ``updated_at`` ``datetime`` (ISODate) columns.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from backend.db.mongo_mirror import MongoMirror


@pytest.fixture
def mirror(monkeypatch):
    """A MongoMirror backed by an in-memory mongomock client."""
    from mongomock_motor import AsyncMongoMockClient

    m = MongoMirror(url="mongodb://localhost:27017", db_name="test_evalops")
    # Swap the real client for an in-memory mock.
    monkeypatch.setattr(
        "motor.motor_asyncio.AsyncIOMotorClient",
        lambda *a, **k: AsyncMongoMockClient(),
    )
    return m


@pytest.mark.asyncio
async def test_mirror_writes_serial_and_timestamps(mirror):
    assert await mirror.start() is True
    try:
        rec = await mirror._db["pipeline_records"].find_one()  # ensure db exists
        # First write -> serial_no 1 + created/updated timestamps.
        await mirror.write("pipeline", {"id": "pl-1", "status": "draft"})
        doc = await mirror._db["pipeline_records"].find_one({"id": "pl-1"})
        assert doc is not None
        assert doc["serial_no"] == 1
        assert isinstance(doc["created_at"], datetime)
        assert isinstance(doc["updated_at"], datetime)

        # Second write (different record) -> serial_no 2.
        await mirror.write("pipeline", {"id": "pl-2", "status": "draft"})
        doc2 = await mirror._db["pipeline_records"].find_one({"id": "pl-2"})
        assert doc2["serial_no"] == 2

        # Update of pl-1 -> serial_no preserved, updated_at changes, created_at kept.
        first_created = doc["created_at"]
        await mirror.write("pipeline", {"id": "pl-1", "status": "running"})
        updated = await mirror._db["pipeline_records"].find_one({"id": "pl-1"})
        assert updated["serial_no"] == 1
        assert updated["status"] == "running"
        assert updated["created_at"] == first_created
        assert updated["updated_at"] >= first_created
    finally:
        await mirror.stop()


@pytest.mark.asyncio
async def test_mirror_delete(mirror):
    assert await mirror.start() is True
    try:
        await mirror.write("trace", {"id": "tr-1", "status": "running"})
        assert await mirror._db["trace_records"].count_documents({}) == 1
        await mirror.delete("trace", "tr-1")
        assert await mirror._db["trace_records"].count_documents({}) == 0
    finally:
        await mirror.stop()


@pytest.mark.asyncio
async def test_mirror_inactive_when_no_url():
    m = MongoMirror(url="")
    assert await m.start() is False
    # write must be a no-op and not raise.
    await m.write("pipeline", {"id": "x"})
    assert m.is_active is False


if __name__ == "__main__":
    asyncio.run(test_mirror_writes_serial_and_timestamps(MongoMirror(url="x")))
