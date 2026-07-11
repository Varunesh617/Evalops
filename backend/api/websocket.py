"""WebSocket endpoint for streaming trace events in real-time."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["websocket"])


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket connections with topic-based filtering."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._subscriptions: dict[WebSocket, dict[str, Any]] = {}

    async def connect(self, ws: WebSocket, subscription: dict[str, Any]) -> None:
        await ws.accept()
        self._connections.append(ws)
        self._subscriptions[ws] = subscription
        logger.info(
            "ws_connected",
            total=len(self._connections),
            pipeline_id=subscription.get("pipeline_id"),
            trace_id=subscription.get("trace_id"),
        )

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]
        self._subscriptions.pop(ws, None)
        logger.info("ws_disconnected", total=len(self._connections))

    def _matches(self, ws: WebSocket, event: dict[str, Any]) -> bool:
        sub = self._subscriptions.get(ws, {})
        if sub.get("pipeline_id") and event.get("pipeline_id") != sub["pipeline_id"]:
            return False
        if sub.get("trace_id") and event.get("trace_id") != sub["trace_id"]:
            return False
        allowed = sub.get("event_types")
        if allowed and event.get("event_type") not in allowed:
            return False
        return True

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send an event to all matching connections."""
        stale: list[WebSocket] = []
        for ws in self._connections:
            if self._matches(ws, event):
                try:
                    await ws.send_json(event)
                except Exception:
                    stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


_manager: ConnectionManager | None = None


def get_manager() -> ConnectionManager:
    """Return the singleton ConnectionManager, creating it on first call."""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.websocket("/ws/traces")
async def trace_stream(
    ws: WebSocket,
    manager: ConnectionManager = Depends(get_manager),
) -> None:
    """WebSocket endpoint for streaming trace events.

    On connect the client should send a JSON subscription message:

    {
      "pipeline_id": "pl-abc123",   // optional filter
      "trace_id": "tr-def456",      // optional filter
      "event_types": ["trace_start", "trace_step", "trace_end"]
    }

    After that the server streams matching events as they occur.
    """

    # Wait for subscription message (timeout 10s)
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        subscription = json.loads(raw)
    except (TimeoutError, json.JSONDecodeError):
        subscription = {}

    await manager.connect(ws, subscription)

    try:
        while True:
            # Keep connection alive; clients can send pings or updated subscriptions
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Allow subscription updates
            if "pipeline_id" in msg or "trace_id" in msg or "event_types" in msg:
                manager._subscriptions[ws].update(msg)
                await ws.send_json({"event_type": "subscription_updated", "data": msg})

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


async def emit_trace_event(event: dict[str, Any]) -> None:
    """Helper used by pipeline runners / tracer to push events."""
    await get_manager().broadcast(event)
