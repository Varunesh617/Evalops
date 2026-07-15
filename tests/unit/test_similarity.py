"""Tests for embedding-based similarity helpers (backend.eval.similarity)."""

from __future__ import annotations

import importlib

import pytest

import backend.eval.similarity as similarity
from backend.eval.similarity import (
    EmbeddingsUnavailableError,
    EmbeddingBackend,
    embed_similarity,
    embed_similarity_to_chunks,
)


def test_module_imports_without_optional_dep():
    # The module must import even when sentence-transformers is absent.
    assert similarity.EmbeddingBackend is not None


def test_local_provider_default():
    assert EmbeddingBackend.provider() == "local"


def test_embed_similarity_unavailable_returns_zero(monkeypatch):
    # Force the loader to fail so the backend is unavailable.
    def _boom(*a, **k):
        raise EmbeddingsUnavailableError("forced")

    monkeypatch.setattr(EmbeddingBackend, "_load_local_model", staticmethod(_boom))
    assert embed_similarity("a", "b") == 0.0


def test_embed_similarity_to_chunks_unavailable_returns_empty(monkeypatch):
    def _boom(*a, **k):
        raise EmbeddingsUnavailableError("forced")

    monkeypatch.setattr(EmbeddingBackend, "_load_local_model", staticmethod(_boom))
    assert embed_similarity_to_chunks("a", ["b", "c"]) == []


def test_hybrid_fallback_to_token_when_embed_unavailable(monkeypatch):
    # With embeddings broken, the helper must return [] (graceful) so the
    # calling metric can fall back to token overlap instead of raising.
    def _boom(*a, **k):
        raise EmbeddingsUnavailableError("forced")

    monkeypatch.setattr(EmbeddingBackend, "_load_local_model", staticmethod(_boom))

    sims = embed_similarity_to_chunks("hello world", ["hello world"])
    assert sims == []


def test_encode_openai_uses_httpx(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(similarity.httpx, "Client", _Client)
    monkeypatch.setenv("EVALOPS_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("EVALOPS_EMBED_API_KEY", "sk-test")

    vectors = EmbeddingBackend.encode(["hello"])
    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured["url"].endswith("/embeddings")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    # API key must not be logged.
    assert "sk-test" not in str(captured["json"])
