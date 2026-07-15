"""Shared embedding + similarity helpers for evaluation metrics.

This module is intentionally dependency-light: ``sentence-transformers`` is an
OPTIONAL dependency and is imported lazily so that a base ``pip install`` of
EvalOps (without the ``embeddings`` extra) keeps working — the import helper
raises a named :class:`EmbeddingsUnavailableError` instead of failing at import
time.

An OpenAI-compatible HTTP embedding path is also supported behind the
``EVALOPS_EMBED_PROVIDER=openai`` environment variable (uses ``httpx``, which
is already a base dependency).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from backend.eval.metrics.base import BaseMetric

logger = structlog.get_logger(__name__)

DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"


class EmbeddingsUnavailableError(RuntimeError):
    """Raised when an embedding backend cannot be initialised."""


class EmbeddingBackend:
    """Lazy, cached embedding backend.

    The sentence-transformers model is loaded once (on first use) and cached at
    module level. If the optional dependency is missing — or the configured
    provider is unreachable — operations raise :class:`EmbeddingsUnavailableError`
    and callers are expected to fall back to token-overlap similarity.
    """

    _MODEL: Any | None = None
    _OPENAI_CLIENT_READY: bool | None = None

    @classmethod
    def provider(cls) -> str:
        return os.getenv("EVALOPS_EMBED_PROVIDER", "local").lower()

    @classmethod
    def model_name(cls) -> str:
        return os.getenv("EVALOPS_EMBED_MODEL", DEFAULT_EMBED_MODEL)

    @classmethod
    def _load_local_model(cls) -> Any:
        if cls._MODEL is not None:
            return cls._MODEL
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised via monkeypatch
            raise EmbeddingsUnavailableError(
                "sentence-transformers is not installed; install with "
                "'pip install evalops[embeddings]' or set "
                "EVALOPS_EMBED_PROVIDER=openai"
            ) from exc
        cls._MODEL = SentenceTransformer(cls.model_name())
        return cls._MODEL

    @classmethod
    def encode(cls, texts: list[str]) -> list[list[float]]:
        """Encode *texts* into embedding vectors.

        Raises :class:`EmbeddingsUnavailableError` if no backend is available.
        """
        if not texts:
            return []
        if cls.provider() == "openai":
            return cls._encode_openai(texts)
        model = cls._load_local_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]

    @classmethod
    def _encode_openai(cls, texts: list[str]) -> list[list[float]]:
        base_url = os.getenv("EVALOPS_EMBED_BASE_URL", "https://api.openai.com/v1/embeddings")
        api_key = os.getenv("EVALOPS_EMBED_API_KEY")
        if not api_key:
            raise EmbeddingsUnavailableError(
                "EVALOPS_EMBED_API_KEY is not set for the openai embedding provider"
            )
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.post(
                    base_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": cls.model_name(), "input": texts},
                )
                resp.raise_for_status()
                data = resp.json()
            return [[float(x) for x in item["embedding"]] for item in data["data"]]
        except Exception as exc:
            raise EmbeddingsUnavailableError(
                f"OpenAI-compatible embedding request failed: {exc}"
            ) from exc


def embed_similarity(text_a: str, text_b: str) -> float:
    """Cosine similarity between two texts via embeddings.

    Returns ``0.0`` if the embedding backend is unavailable so callers can
    decide on a token-overlap fallback.
    """
    try:
        vectors = EmbeddingBackend.encode([text_a, text_b])
    except EmbeddingsUnavailableError:
        return 0.0
    if len(vectors) < 2:
        return 0.0
    return float(BaseMetric.cosine_similarity_simple(vectors[0], vectors[1]))


def embed_similarity_to_chunks(text: str, chunks: list[str]) -> list[float]:
    """Cosine similarity of *text* against each chunk.

    Returns an empty list if the backend is unavailable.
    """
    if not chunks:
        return []
    try:
        vectors = EmbeddingBackend.encode([text, *chunks])
    except EmbeddingsUnavailableError:
        return []
    if not vectors:
        return []
    base = vectors[0]
    return [float(BaseMetric.cosine_similarity_simple(base, v)) for v in vectors[1:]]
