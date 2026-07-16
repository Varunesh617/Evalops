"""Unit tests for the Settings routes (provider CRUD + test-connection)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.api.app import create_app
from backend.api.routes import plugins as plugins_route
from backend.core.config import ProviderConfig, Settings, get_settings
from backend.core.llm_client import LLMClient, LLMClientError

API_KEY = "test-admin-key"


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Point providers persistence at a temp file and reset the singleton.
    providers_file = tmp_path / "providers.json"
    monkeypatch.setattr(Settings, "providers_file", providers_file)
    monkeypatch.setattr(plugins_route, "_REQUIRED_API_KEY", API_KEY)
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-API-Key": API_KEY}


# --- helpers ---------------------------------------------------------------


def _fake_openai_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "pong", "role": "assistant"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# GET lists masked keys
# ---------------------------------------------------------------------------


class TestListProviders:
    @pytest.mark.asyncio
    async def test_keys_masked(self, client):
        resp = await client.get("/settings/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "active_provider" in data
        assert "llm_enabled" in data
        for p in data["providers"]:
            assert p["api_key_state"] in ("set", "unset")
            assert "sk-" not in json.dumps(p)


# ---------------------------------------------------------------------------
# POST add then GET reflects it
# ---------------------------------------------------------------------------


class TestUpsertProvider:
    @pytest.mark.asyncio
    async def test_add_then_listed(self, client, auth_headers):
        body = {
            "name": "my-openai",
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-secret-123",
            "default_model": "gpt-4o",
            "is_default": True,
        }
        resp = await client.post("/settings/providers", json=body, headers=auth_headers)
        assert resp.status_code == 201
        created = resp.json()
        assert created["name"] == "my-openai"
        assert created["api_key_state"] == "set"
        assert "api_key" not in created

        lst = await client.get("/settings/providers")
        names = [p["name"] for p in lst.json()["providers"]]
        assert "my-openai" in names

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        body = {
            "name": "x",
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
        }
        resp = await client.post("/settings/providers", json=body)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_update_existing(self, client, auth_headers):
        body = {"name": "openai", "kind": "openai", "base_url": "https://api.openai.com/v1"}
        await client.post("/settings/providers", json=body, headers=auth_headers)

        upd = {
            "name": "openai",
            "kind": "openai",
            "base_url": "https://custom.openai.com/v1",
            "api_key": "sk-new",
            "default_model": "gpt-4o-mini",
        }
        resp = await client.post("/settings/providers", json=upd, headers=auth_headers)
        assert resp.status_code == 201
        lst = await client.get("/settings/providers")
        openai = next(p for p in lst.json()["providers"] if p["name"] == "openai")
        assert openai["base_url"] == "https://custom.openai.com/v1"
        assert openai["default_model"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# DELETE removes; cannot delete last
# ---------------------------------------------------------------------------


class TestDeleteProvider:
    @pytest.mark.asyncio
    async def test_delete_and_persist(self, client, auth_headers):
        # add a throwaway provider
        add = {
            "name": "temp",
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
        }
        await client.post("/settings/providers", json=add, headers=auth_headers)

        resp = await client.delete("/settings/providers/temp", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        lst = await client.get("/settings/providers")
        assert "temp" not in [p["name"] for p in lst.json()["providers"]]

    @pytest.mark.asyncio
    async def test_cannot_delete_last(self, client, auth_headers, tmp_path, monkeypatch):
        # Force only one provider
        pf = tmp_path / "single.json"
        pf.write_text(json.dumps([
            {"name": "only", "kind": "openai", "base_url": "https://api.openai.com/v1", "is_default": True}
        ]), encoding="utf-8")
        monkeypatch.setenv("EVALOPS_PROVIDERS_FILE", str(pf))
        get_settings.cache_clear()

        resp = await client.delete("/settings/providers/only", headers=auth_headers)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.delete("/settings/providers/openai")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Active switch + llm_enabled toggle
# ---------------------------------------------------------------------------


class TestActiveProvider:
    @pytest.mark.asyncio
    async def test_set_active_and_toggle(self, client, auth_headers):
        resp = await client.put(
            "/settings/providers/active",
            json={"active_provider": "anthropic", "llm_enabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_provider"] == "anthropic"
        assert data["llm_enabled"] is True

    @pytest.mark.asyncio
    async def test_unknown_active_404(self, client, auth_headers):
        resp = await client.put(
            "/settings/providers/active",
            json={"active_provider": "nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.put(
            "/settings/providers/active", json={"active_provider": "openai"}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test-connection with mocked transport
# ---------------------------------------------------------------------------


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_success(self, client, monkeypatch):
        captured: list[LLMClient] = []

        def fake_from_provider(self, *, model=None, timeout=60.0):
            client = LLMClient(
                model=model or "gpt-4o",
                base_url="https://api.openai.com/v1",
                api_key="sk-x",
                timeout=timeout,
            )
            client._transport = _fake_openai_transport()
            captured.append(client)
            return client

        monkeypatch.setattr(LLMClient, "from_provider", fake_from_provider)

        resp = await client.post(
            "/settings/providers/test",
            json={"name": "openai", "model": "gpt-4o"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_failure_returns_ok_false(self, client, monkeypatch):
        def boom(*args, **kwargs):
            raise LLMClientError("connection refused")

        monkeypatch.setattr(LLMClient, "from_provider", staticmethod(boom))

        resp = await client.post(
            "/settings/providers/test", json={"name": "openai", "model": "gpt-4o"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "connection refused"

    @pytest.mark.asyncio
    async def test_custom_construct_directly(self, client, monkeypatch):
        made: dict = {}

        real_init = LLMClient.__init__

        def fake_init(self, model=None, *, base_url=None, api_key=None, provider="auto", timeout=60.0, transport=None):
            made.update(model=model, base_url=base_url, provider=provider)
            real_init(
                self,
                model,
                base_url=base_url,
                api_key=api_key,
                provider=provider,
                timeout=timeout,
                transport=_fake_openai_transport(),
            )

        monkeypatch.setattr(LLMClient, "__init__", fake_init)

        resp = await client.post(
            "/settings/providers/test",
            json={
                "kind": "ollama",
                "base_url": "http://localhost:11434/v1",
                "api_key": "ignored",
                "model": "llama3",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert made["provider"] == "ollama"
        assert made["base_url"] == "http://localhost:11434/v1"
