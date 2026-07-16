"""Tests for the LLM provider registry (Settings + ProviderConfig) and
LLMClient.from_provider wiring.
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from backend.core.config import (
    LLMProviderKind,
    ProviderConfig,
    Settings,
    get_settings,
)
from backend.core.llm_client import LLMClient


@pytest.fixture
def temp_providers_file(tmp_path, monkeypatch):
    """Point the providers file at a temp path and invalidate the cache."""
    path = tmp_path / "providers.json"
    if path.exists():
        path.unlink()
    monkeypatch.setenv("EVALOPS_PROVIDERS_FILE", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
    if path.exists():
        path.unlink()


class TestLLMProviderKind:
    def test_values(self):
        assert LLMProviderKind.OPENAI == "openai"
        assert LLMProviderKind.ANTHROPIC == "anthropic"
        assert LLMProviderKind.OLLAMA == "ollama"
        assert LLMProviderKind.OPENROUTER == "openrouter"
        assert LLMProviderKind.CUSTOM == "custom"

    def test_membership(self):
        assert len(LLMProviderKind) == 5


class TestProviderConfig:
    def test_safe_dump_masks_key(self):
        cfg = ProviderConfig(
            name="openai",
            kind=LLMProviderKind.OPENAI,
            base_url="https://api.openai.com/v1",
            api_key=SecretStr("sk-secret"),
        )
        dumped = cfg.safe_dump()
        assert dumped["api_key"] == "***"
        # internal value is untouched
        assert cfg.api_key.get_secret_value() == "sk-secret"

    def test_safe_dump_no_key(self):
        cfg = ProviderConfig(
            name="ollama",
            kind=LLMProviderKind.OLLAMA,
            base_url="http://localhost:11434/v1",
        )
        dumped = cfg.safe_dump()
        assert dumped["api_key"] is None


class TestSettingsSeeding:
    def test_seeds_four_defaults(self, temp_providers_file):
        if temp_providers_file.exists():
            temp_providers_file.unlink()
        get_settings.cache_clear()
        providers = Settings.load_providers()
        assert len(providers) == 4
        names = {p.name for p in providers}
        assert names == {"openai", "anthropic", "ollama", "openrouter"}
        openai = next(p for p in providers if p.name == "openai")
        assert openai.is_default is True
        assert openai.base_url == "https://api.openai.com/v1"

    def test_seed_writes_file(self, temp_providers_file):
        Settings.load_providers()
        assert temp_providers_file.exists()


class TestProvidersRoundtrip:
    def test_save_load_roundtrip(self, temp_providers_file):
        providers = [
            ProviderConfig(
                name="openai",
                kind=LLMProviderKind.OPENAI,
                base_url="https://api.openai.com/v1",
                api_key=SecretStr("sk-test"),
                default_model="gpt-4o",
                is_default=True,
            ),
            ProviderConfig(
                name="ollama",
                kind=LLMProviderKind.OLLAMA,
                base_url="http://localhost:11434/v1",
            ),
        ]
        Settings.save_providers(providers)
        loaded = Settings.load_providers()
        assert len(loaded) == 2
        assert loaded[0].api_key.get_secret_value() == "sk-test"
        assert loaded[1].kind == LLMProviderKind.OLLAMA

    def test_load_reads_existing_file(self, temp_providers_file):
        data = [
            {
                "name": "custom",
                "kind": "custom",
                "base_url": "http://example/v1",
                "default_model": "m1",
            }
        ]
        temp_providers_file.write_text(json.dumps(data), encoding="utf-8")
        loaded = Settings.load_providers()
        assert len(loaded) == 1
        assert loaded[0].name == "custom"
        assert loaded[0].kind == LLMProviderKind.CUSTOM


class TestFromProvider:
    def test_openai_mapping(self, temp_providers_file):
        Settings.save_providers(Settings.load_providers())
        client = LLMClient.from_provider("openai")
        assert client.base_url == "https://api.openai.com/v1"
        assert client.provider == "openai"

    def test_ollama_maps_to_openai_protocol(self, temp_providers_file):
        Settings.save_providers(Settings.load_providers())
        client = LLMClient.from_provider("ollama")
        assert client.base_url == "http://localhost:11434/v1"
        assert client.provider == "openai"

    def test_openrouter_maps_to_openai_protocol(self, temp_providers_file):
        Settings.save_providers(Settings.load_providers())
        client = LLMClient.from_provider("openrouter")
        assert client.base_url == "https://openrouter.ai/api/v1"
        assert client.provider == "openai"

    def test_anthropic_mapping(self, temp_providers_file):
        Settings.save_providers(Settings.load_providers())
        client = LLMClient.from_provider("anthropic")
        assert client.base_url == "https://api.anthropic.com/v1"
        assert client.provider == "anthropic"

    def test_explicit_model_overrides_default(self, temp_providers_file):
        providers = Settings.load_providers()
        for p in providers:
            if p.name == "openai":
                p.default_model = "gpt-4o-mini"
        Settings.save_providers(providers)
        client = LLMClient.from_provider("openai", model="gpt-4o")
        assert client.model == "gpt-4o"

    def test_uses_default_model_when_none(self, temp_providers_file):
        providers = Settings.load_providers()
        for p in providers:
            if p.name == "openai":
                p.default_model = "gpt-4o-mini"
        Settings.save_providers(providers)
        client = LLMClient.from_provider("openai")
        assert client.model == "gpt-4o-mini"

    def test_api_key_passed_through(self, temp_providers_file):
        providers = Settings.load_providers()
        for p in providers:
            if p.name == "openai":
                p.api_key = SecretStr("sk-secret")
        Settings.save_providers(providers)
        client = LLMClient.from_provider("openai")
        assert client.api_key == "sk-secret"

    def test_missing_provider_raises(self, temp_providers_file):
        Settings.save_providers(Settings.load_providers())
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            LLMClient.from_provider("olama")
