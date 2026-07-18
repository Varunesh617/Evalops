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

import asyncio
import subprocess
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


async def fetch_model_details(
    base_url: str,
    model_name: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fetch architecture details for a local model via Ollama ``/api/show``.

    Returns a structured dict.  Never raises — any error is captured into the
    returned dict.  Only attempted for local endpoints; cloud endpoints get a
    note instead.
    """
    if not is_local_endpoint(base_url):
        return {
            "name": model_name,
            "note": "model-details only available for local endpoints",
        }

    origin = _strip_to_origin(base_url)
    try:
        async with httpx.AsyncClient(
            timeout=3.0, follow_redirects=True, transport=transport
        ) as client:
            resp = await client.post(
                f"{origin}/api/show", json={"model": model_name}
            )
            resp.raise_for_status()
            payload = resp.json()

        details = payload.get("details", {}) or {}
        if not isinstance(details, dict):
            details = {}
        model_info = payload.get("model_info", {}) or {}
        if not isinstance(model_info, dict):
            model_info = {}

        families = details.get("families") or []
        family = details.get("family")
        if not family and isinstance(families, list) and families:
            family = families[0]

        layer_count = model_info.get("ssm_block_count")
        if layer_count is None:
            layer_count = model_info.get("block_count")

        return {
            "name": model_name,
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "context_length": details.get("context_length"),
            "format": details.get("format"),
            "family": family,
            "layer_count": layer_count,
            "details_ok": True,
            "error": None,
        }
    except (httpx.TimeoutException, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        return {
            "name": model_name,
            "parameter_size": None,
            "quantization_level": None,
            "context_length": None,
            "format": None,
            "family": None,
            "layer_count": None,
            "details_ok": False,
            "error": str(exc),
        }


def _gpu_stats_sync() -> dict[str, Any]:
    """Synchronously query ``nvidia-smi`` for GPU stats (runs in a thread)."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode != 0:
            return {"available": False, "error": f"nvidia-smi rc={proc.returncode}"}

        line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        name, used, total, util = [part.strip() for part in line.split(",")]
        used_mib = int(used.replace("MiB", "").strip())
        total_mib = int(total.replace("MiB", "").strip())
        util_pct = float(util.replace("%", "").strip())

        return {
            "available": True,
            "gpu_name": name,
            "vram_used_mib": used_mib,
            "vram_total_mib": total_mib,
            "gpu_utilization_pct": util_pct,
            "error": None,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError, IndexError) as exc:
        return {"available": False, "error": str(exc)}


async def fetch_gpu_stats() -> dict[str, Any]:
    """Fetch GPU stats by running ``nvidia-smi`` in a thread. Never raises."""
    return await asyncio.to_thread(_gpu_stats_sync)


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
                if models:
                    model_details = await fetch_model_details(
                        base_url, models[0], transport=transport
                    )
                else:
                    model_details = None
                gpu = await fetch_gpu_stats()
                return {
                    "source": "local",
                    "kind": "ollama",
                    "available": True,
                    "models": models,
                    "model_details": model_details,
                    "gpu": gpu,
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
            gpu = await fetch_gpu_stats()
            return {
                "source": "local",
                "kind": "unknown-local",
                "available": available,
                "models": [],
                "model_details": None,
                "gpu": gpu,
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
            "model_details": None,
            "gpu": await fetch_gpu_stats(),
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
