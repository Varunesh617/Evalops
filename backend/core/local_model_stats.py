"""Optional visibility into local OpenAI-compatible model backends.

When the evaluated LLM is a self-hosted server (Ollama, LM Studio, vLLM,
LocalAI, …) we can probe a couple of safe, unauthenticated endpoints to learn
where effort was spent inside the local model.  Cloud endpoints are never
contacted — :func:`is_local_endpoint` gates all probing.

The module is deliberately fail-soft: every public function swallows network
errors and returns a structured dict instead of raising, so attaching stats to
a trace payload can never break the pipeline.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog

logger = structlog.get_logger(__name__)

_LOCAL_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0")


def is_local_endpoint(base_url: str | None) -> bool:
    """Return True for self-hosted/local LLM endpoints.

    Recognition is purely host-based: any URL whose host contains one of the
    loopback markers (``localhost``, ``127.0.0.1``, ``0.0.0.0``) is treated as
    local.  Cloud hosts (``api.openai.com``, ``anthropic``, ``openrouter``) and
    ``None`` return False.
    """
    if not base_url:
        return False
    try:
        host = urlsplit(base_url).hostname or ""
    except ValueError:
        return False
    lowered = host.lower()
    return any(marker in lowered for marker in _LOCAL_HOST_MARKERS)


def _strip_to_origin(base_url: str) -> str:
    """Reduce ``http://localhost:11434/v1`` to ``http://localhost:11434``."""
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


async def fetch_local_stats(
    base_url: str,
    *,
    timeout: float = 3.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Probe a local LLM server for lightweight backend stats.

    The probe is safe and unauthenticated:

    - Ollama exposes ``GET {origin}/api/tags`` (list of models).
    - vLLM / LM Studio / others have no standard unauthenticated stats
      endpoint, so a generic ``GET {origin}/`` probe is used: any 2xx means the
      server is up.

    Never raises — connection errors are captured into the returned dict.
    """
    origin = _strip_to_origin(base_url)
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport
        ) as client:
            resp = await client.get(f"{origin}/api/tags")
            if resp.status_code < 300:
                models: list[Any] = []
                try:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        raw_models = payload.get("models", [])
                        if isinstance(raw_models, list):
                            models = [
                                m.get("name", m) if isinstance(m, dict) else m
                                for m in raw_models
                            ]
                except ValueError:
                    pass
                return {
                    "source": "local",
                    "kind": "ollama",
                    "available": True,
                    "models": models,
                    "latency_probe_ms": (time.perf_counter() - start) * 1000.0,
                    "error": None,
                }
    except (httpx.TimeoutException, httpx.HTTPError, OSError, ValueError):
        # Not Ollama (or probe failed) — fall through to the generic probe.
        pass

    # Generic probe for non-Ollama local servers (vLLM, LM Studio, …).
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport
        ) as client:
            resp = await client.get(f"{origin}/")
            available = resp.status_code < 400
            return {
                "source": "local",
                "kind": "unknown-local",
                "available": available,
                "models": [],
                "latency_probe_ms": (time.perf_counter() - start) * 1000.0,
                "error": None if available else f"status {resp.status_code}",
            }
    except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
        logger.debug("local_stats_probe_failed", base_url=base_url, error=str(exc))
        return {
            "source": "local",
            "kind": "unknown-local",
            "available": False,
            "models": [],
            "latency_probe_ms": (time.perf_counter() - start) * 1000.0,
            "error": str(exc),
        }


async def attach_local_stats(
    step_payload: dict,
    base_url: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Attach backend stats to *step_payload* when the endpoint is local.

    For cloud (``None`` or non-local) endpoints this is a no-op.  On local
    endpoints it sets ``step_payload["local_backend"]`` to the result of
    :func:`fetch_local_stats`.
    """
    if not is_local_endpoint(base_url):
        return
    assert base_url is not None
    step_payload["local_backend"] = await fetch_local_stats(
        base_url, transport=transport
    )
