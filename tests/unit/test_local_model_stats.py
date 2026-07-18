"""Tests for backend.core.local_model_stats.

Covers endpoint classification, the (transport-injected) async stats fetch,
and the fail-soft attach helper.  ``asyncio_mode = auto`` so async tests run
without explicit markers.
"""

from __future__ import annotations

import httpx
import pytest

from backend.core.local_model_stats import (
    attach_local_stats,
    fetch_local_stats,
    is_local_endpoint,
)


# ---------------------------------------------------------------------------
# is_local_endpoint truth table
# ---------------------------------------------------------------------------


class TestIsLocalEndpoint:
    @pytest.mark.parametrize(
        "base_url,expected",
        [
            ("http://localhost:8000", True),
            ("http://localhost:11434/v1", True),
            ("http://127.0.0.1:11434", True),
            ("http://0.0.0.0:1234", True),
            ("https://api.openai.com/v1", False),
            ("https://api.anthropic.com", False),
            ("https://openrouter.ai/api/v1", False),
            (None, False),
            ("", False),
        ],
    )
    def test_truth_table(self, base_url, expected):
        assert is_local_endpoint(base_url) is expected


# ---------------------------------------------------------------------------
# fetch_local_stats — async probes with injected transport
# ---------------------------------------------------------------------------


class TestFetchLocalStats:
    async def test_ollama_tags_probe(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "llama3"}]})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        stats = await fetch_local_stats("http://localhost:11434/v1", transport=transport)

        assert stats["available"] is True
        assert stats["kind"] == "ollama"
        assert stats["models"] == ["llama3"]
        assert stats["error"] is None

    async def test_tags_404_falls_back_to_generic_probe(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(404)
            if request.url.path == "/":
                return httpx.Response(200)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        stats = await fetch_local_stats("http://localhost:11434/v1", transport=transport)

        # Generic probe returns 2xx -> available True.
        assert stats["available"] is True
        assert stats["kind"] == "unknown-local"

    async def test_unreachable_marked_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        stats = await fetch_local_stats("http://localhost:11434/v1", transport=transport)

        assert stats["available"] is False
        assert stats["error"] is not None


# ---------------------------------------------------------------------------
# attach_local_stats — fail-soft mutates payload for local endpoints
# ---------------------------------------------------------------------------


class TestAttachLocalStats:
    async def test_noop_for_cloud_endpoint(self):
        payload: dict = {"foo": "bar"}
        await attach_local_stats(payload, "https://api.openai.com/v1")
        assert "local_backend" not in payload

    async def test_noop_for_none(self):
        payload: dict = {}
        await attach_local_stats(payload, None)
        assert "local_backend" not in payload

    async def test_attaches_for_local_endpoint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": []})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        payload: dict = {"foo": "bar"}
        await attach_local_stats(payload, "http://localhost:8000/v1", transport=transport)
        # attach_local_stats mutates the passed payload in place.
        assert payload.get("local_backend", {}).get("available") is True
