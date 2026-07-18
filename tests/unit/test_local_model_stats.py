"""Tests for backend.core.local_model_stats.

Covers endpoint classification, the (transport-injected) async stats fetch,
and the fail-soft attach helper.  ``asyncio_mode = auto`` so async tests run
without explicit markers.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import httpx
import pytest

from backend.core.local_model_stats import (
    attach_local_stats,
    fetch_gpu_stats,
    fetch_local_stats,
    fetch_model_details,
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


# ---------------------------------------------------------------------------
# fetch_model_details — /api/show architecture details (Ollama)
# ---------------------------------------------------------------------------


class TestFetchModelDetails:
    async def test_details_parsed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/show"
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "details": {
                        "parameter_size": "7B",
                        "quantization_level": "Q4_K_M",
                        "context_length": 8192,
                        "format": "gguf",
                        "family": "llama",
                    },
                    "model_info": {},
                },
            )

        transport = httpx.MockTransport(handler)
        details = await fetch_model_details(
            "http://localhost:11434/v1", "llama3", transport=transport
        )

        assert details["details_ok"] is True
        assert details["parameter_size"] == "7B"
        assert details["quantization_level"] == "Q4_K_M"
        assert details["context_length"] == 8192
        assert details["error"] is None

    async def test_non_local_returns_note(self):
        details = await fetch_model_details("https://api.openai.com/v1", "gpt-4")
        assert details["note"] == "model-details only available for local endpoints"


# ---------------------------------------------------------------------------
# fetch_gpu_stats — nvidia-smi parsing (subprocess monkeypatched)
# ---------------------------------------------------------------------------


class TestFetchGpuStats:
    async def test_gpu_parsed(self):
        cp = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="NVIDIA GeForce RTX 4050 Laptop GPU, 1374 MiB, 6141 MiB, 4 %\n",
            stderr="",
        )
        with patch("backend.core.local_model_stats.subprocess.run", return_value=cp):
            gpu = await fetch_gpu_stats()

        assert gpu["available"] is True
        assert gpu["gpu_name"] == "NVIDIA GeForce RTX 4050 Laptop GPU"
        assert gpu["vram_total_mib"] == 6141
        assert gpu["gpu_utilization_pct"] == 4.0
        assert gpu["error"] is None

    async def test_nonzero_returncode(self):
        cp = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        )
        with patch("backend.core.local_model_stats.subprocess.run", return_value=cp):
            gpu = await fetch_gpu_stats()

        assert gpu["available"] is False
        assert gpu["error"] is not None


# ---------------------------------------------------------------------------
# fetch_local_stats — enriched with model_details and gpu
# ---------------------------------------------------------------------------


class TestFetchLocalStatsEnriched:
    async def test_enriched(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "llama3"}]})
            if request.url.path == "/api/show":
                return httpx.Response(
                    200,
                    json={
                        "details": {
                            "parameter_size": "7B",
                            "quantization_level": "Q4_K_M",
                            "context_length": 8192,
                            "format": "gguf",
                            "family": "llama",
                        },
                        "model_info": {},
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        cp = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="NVIDIA GeForce RTX 4050 Laptop GPU, 1374 MiB, 6141 MiB, 4 %\n",
            stderr="",
        )
        with patch("backend.core.local_model_stats.subprocess.run", return_value=cp):
            stats = await fetch_local_stats(
                "http://localhost:11434/v1", transport=transport
            )

        assert "model_details" in stats
        assert stats["model_details"]["details_ok"] is True
        assert stats["model_details"]["parameter_size"] == "7B"
        assert "gpu" in stats
        assert stats["gpu"]["available"] is True
        assert stats["gpu"]["vram_total_mib"] == 6141
