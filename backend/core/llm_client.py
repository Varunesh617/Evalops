"""Unified async LLM client for EvalOps pipeline steps.

The client speaks the **OpenAI Chat Completions** protocol, which means it
works out-of-the-box with any OpenAI-compatible endpoint:

- OpenAI (``https://api.openai.com/v1``)
- OpenRouter, Together, Groq, Fireworks, DeepInfra, etc.
- Self-hosted: vLLM, TGI, LM Studio, Ollama (``/v1``), LocalAI

For Anthropic Claude, set ``provider="anthropic"`` (or
``EVALOPS_LLM_PROVIDER=anthropic``); requests are translated to the
``/v1/messages`` shape. This keeps a single call-site for every step.

All configuration is env-gated with per-config overrides. API keys are read
through :func:`os.getenv` (or passed explicitly) and never logged.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
import structlog

logger = structlog.get_logger(__name__)

LLMProvider = Literal["openai", "anthropic", "auto"]

_DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
_DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com/v1"


class LLMClientError(RuntimeError):
    """Raised when an LLM request fails or returns an unexpected shape."""


class LLMClient:
    """Minimal async LLM client supporting OpenAI-compatible + Anthropic.

    Resolution order for connection settings (first wins):

    1. explicit constructor arguments
    2. ``EVALOPS_LLM_*`` environment variables
    3. provider defaults
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        provider: LLMProvider = "auto",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model or os.getenv("EVALOPS_LLM_MODEL", "gpt-4o")
        self.provider = self._resolve_provider(provider, base_url)
        self.base_url = (base_url or self._default_url_for(self.provider)).rstrip("/")
        self.api_key = api_key or os.getenv("EVALOPS_LLM_API_KEY")
        self.timeout = timeout
        self._transport = transport
        self._log = logger.bind(component="llm_client", provider=self.provider)

    @staticmethod
    def _resolve_provider(provider: LLMProvider, base_url: str | None) -> LLMProvider:
        if provider != "auto":
            return provider
        env_provider = os.getenv("EVALOPS_LLM_PROVIDER", "").lower()
        if env_provider in ("anthropic", "openai"):
            return env_provider  # type: ignore[return-value]
        if base_url and "anthropic" in base_url.lower():
            return "anthropic"  # type: ignore[return-value]
        return "openai"  # type: ignore[return-value]

    @staticmethod
    def _default_url_for(provider: LLMProvider) -> str:
        if provider == "anthropic":
            return _DEFAULT_ANTHROPIC_URL
        return _DEFAULT_OPENAI_URL

    @classmethod
    def from_provider(
        cls,
        name: str,
        *,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> LLMClient:
        """Build an :class:`LLMClient` from a registered provider by ``name``.

        Looks the provider up in :func:`backend.core.config.get_settings`,
        mapping the registry ``kind`` onto the client's wire protocol:

        - ``ollama`` / ``openrouter`` / ``custom`` -> ``"openai"`` (OpenAI-compatible)
        - ``anthropic`` -> ``"anthropic"``
        - ``openai`` -> ``"openai"``

        Raises :class:`ValueError` if no provider with that ``name`` exists.
        """
        from backend.core.config import get_settings

        provider = next(
            (p for p in get_settings().providers if p.name == name), None
        )
        if provider is None:
            available = ", ".join(p.name for p in get_settings().providers)
            raise ValueError(
                f"Unknown LLM provider '{name}'. Registered: {available or '<none>'}"
            )

        wire_provider: LLMProvider = (
            "anthropic" if provider.kind == "anthropic" else "openai"
        )

        api_key = provider.api_key.get_secret_value() if provider.api_key else None
        resolved_model = model or provider.default_model or None

        return cls(
            resolved_model,
            base_url=provider.base_url,
            api_key=api_key,
            provider=wire_provider,
            timeout=timeout,
        )

    @property
    def configured(self) -> bool:
        """True when the client can attempt a request (key present)."""
        return bool(self.api_key)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send *messages* and return ``{"text", "tokens", "raw"}``.

        Raises :class:`LLMClientError` on missing key, transport error, or an
        unparseable response.
        """
        if not self.api_key:
            raise LLMClientError("LLM API key is not set (api_key / EVALOPS_LLM_API_KEY)")

        if self.provider == "anthropic":
            return await self._complete_anthropic(
                messages, temperature=temperature, max_tokens=max_tokens,
                system=system, timeout=timeout,
            )
        return await self._complete_openai(
            messages, temperature=temperature, max_tokens=max_tokens,
            system=system, timeout=timeout,
        )

    async def _complete_openai(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        system: str | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        payload_messages = list(messages)
        if system:
            payload_messages = [{"role": "system", "content": system}, *messages]
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise LLMClientError(f"LLM request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"LLM request failed: {exc}") from exc
        except ValueError as exc:  # json decode
            raise LLMClientError(f"LLM returned invalid JSON: {exc}") from exc

        try:
            choice = data["choices"][0]["message"]
            text = choice.get("content", "")
            usage = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError(f"Unexpected LLM response shape: {exc}") from exc

        tokens = {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }
        self._log.debug("llm_completion", model=self.model, **tokens)
        return {"text": text, "tokens": tokens, "raw": data}

    async def _complete_anthropic(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        system: str | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        # Anthropic uses a flat messages list with an explicit system param.
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/messages"
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise LLMClientError(f"LLM request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"LLM request failed: {exc}") from exc
        except ValueError as exc:
            raise LLMClientError(f"LLM returned invalid JSON: {exc}") from exc

        try:
            text = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
            usage = data.get("usage", {})
        except (KeyError, TypeError) as exc:
            raise LLMClientError(f"Unexpected LLM response shape: {exc}") from exc

        tokens = {
            "prompt_tokens": int(usage.get("input_tokens", 0)),
            "completion_tokens": int(usage.get("output_tokens", 0)),
            "total_tokens": int(usage.get("input_tokens", 0))
            + int(usage.get("output_tokens", 0)),
        }
        self._log.debug("llm_completion", model=self.model, provider="anthropic", **tokens)
        return {"text": text, "tokens": tokens, "raw": data}
