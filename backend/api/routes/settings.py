"""Settings routes — manage LLM providers for the Settings GUI.

Exposes CRUD for LLM providers plus a connection-test endpoint. Provider
secrets are never returned to the client and never logged. Mutating endpoints
require the ``X-API-Key`` header (see :func:`require_admin_auth`).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr

from backend.api.routes.plugins import require_admin_auth
from backend.core.config import (
    LLMProviderKind,
    ProviderConfig,
    Settings,
    get_settings,
)
from backend.core.llm_client import LLMClient

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Response / masking helpers
# ---------------------------------------------------------------------------


def _to_response(provider: ProviderConfig) -> dict[str, object]:
    """Serialize a provider for the API, masking the api_key ('set'/'unset')."""
    return {
        "name": provider.name,
        "kind": provider.kind.value,
        "base_url": provider.base_url,
        "api_key_state": (
            "set" if provider.api_key and provider.api_key.get_secret_value() else "unset"
        ),
        "default_model": provider.default_model,
        "is_default": provider.is_default,
    }


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ProviderUpsertRequest(BaseModel):
    """Add or update a provider by name."""

    name: str = Field(..., min_length=1, max_length=256)
    kind: LLMProviderKind
    base_url: str | None = None
    api_key: SecretStr | None = None
    default_model: str = Field(default="", max_length=256)
    is_default: bool = False


class ProviderListResponse(BaseModel):
    providers: list[dict[str, object]]
    active_provider: str
    llm_enabled: bool


class ActiveProviderRequest(BaseModel):
    active_provider: str = Field(..., min_length=1)
    llm_enabled: bool | None = None


class ProviderTestRequest(BaseModel):
    name: str = Field(default="", max_length=256)
    kind: LLMProviderKind | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None
    model: str = Field(default="", max_length=256)


class ProviderTestResponse(BaseModel):
    ok: bool
    error: str | None = None
    model: str | None = None


# ---------------------------------------------------------------------------
# Read-only routes
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=ProviderListResponse)
async def list_providers() -> ProviderListResponse:
    """List all configured providers (api_key masked) plus active/llm state."""
    settings: Settings = get_settings()
    providers = Settings.load_providers()
    return ProviderListResponse(
        providers=[_to_response(p) for p in providers],
        active_provider=settings.active_provider,
        llm_enabled=settings.llm_enabled,
    )


# ---------------------------------------------------------------------------
# Mutating routes (auth required)
# ---------------------------------------------------------------------------


@router.post("/providers", response_model=dict[str, object], status_code=201)
async def upsert_provider(
    body: ProviderUpsertRequest,
    _auth: str = Depends(require_admin_auth),
) -> dict[str, object]:
    """Add a provider, or update it in place if the name already exists."""
    providers = Settings.load_providers()

    existing = next((p for p in providers if p.name == body.name), None)
    if existing is not None:
        existing.kind = body.kind
        existing.base_url = body.base_url if body.base_url is not None else existing.base_url
        if body.api_key is not None:
            existing.api_key = body.api_key
        existing.default_model = body.default_model
        existing.is_default = body.is_default
        updated = existing
    else:
        updated = ProviderConfig(
            name=body.name,
            kind=body.kind,
            base_url=body.base_url if body.base_url is not None else "",
            api_key=body.api_key,
            default_model=body.default_model,
            is_default=body.is_default,
        )
        providers.append(updated)

    _enforce_single_default(providers, updated)
    Settings.save_providers(providers)
    logger.info("provider_upserted", name=body.name, kind=body.kind.value)
    return _to_response(updated)


@router.delete("/providers/{name}", response_model=dict[str, object])
async def delete_provider(
    name: str,
    _auth: str = Depends(require_admin_auth),
) -> dict[str, object]:
    """Remove a provider. The last remaining provider cannot be deleted."""
    providers = Settings.load_providers()
    if len(providers) <= 1:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the last remaining provider.",
        )

    kept = [p for p in providers if p.name != name]
    if len(kept) == len(providers):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found.")

    settings = get_settings()
    if settings.active_provider == name:
        settings.active_provider = kept[0].name

    Settings.save_providers(kept)
    logger.info("provider_deleted", name=name)
    return {"name": name, "deleted": True}


@router.put("/providers/active", response_model=ProviderListResponse)
async def set_active_provider(
    body: ActiveProviderRequest,
    _auth: str = Depends(require_admin_auth),
) -> ProviderListResponse:
    """Set the active provider and optionally toggle llm_enabled."""
    providers = Settings.load_providers()
    if not any(p.name == body.active_provider for p in providers):
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{body.active_provider}' does not exist.",
        )

    settings = get_settings()
    settings.active_provider = body.active_provider
    if body.llm_enabled is not None:
        settings.llm_enabled = body.llm_enabled
        logger.info("llm_enabled_toggled", llm_enabled=body.llm_enabled)

    Settings.save_providers(providers)
    logger.info("active_provider_set", name=body.active_provider)
    return ProviderListResponse(
        providers=[_to_response(p) for p in providers],
        active_provider=settings.active_provider,
        llm_enabled=settings.llm_enabled,
    )


@router.post("/providers/test", response_model=ProviderTestResponse)
async def test_provider(body: ProviderTestRequest) -> ProviderTestResponse:
    """Probe a provider connection. Does NOT persist anything.

    If ``name`` matches a configured provider, its base_url/api_key/profile is
    used via :meth:`LLMClient.from_provider`. Otherwise the supplied base_url /
    api_key / kind are used to construct a client directly.
    """
    providers = Settings.load_providers()
    existing = (
        next((p for p in providers if p.name == body.name), None) if body.name else None
    )

    model = body.model or (existing.default_model if existing else "")
    if not model:
        return ProviderTestResponse(ok=False, error="No model specified for test.")

    try:
        if existing is not None:
            client = LLMClient.from_provider(existing.name, model=model, timeout=10.0)
        else:
            provider_literal = (
                body.kind.value if isinstance(body.kind, LLMProviderKind) else "openai"
            )
            api_key = body.api_key.get_secret_value() if body.api_key else None
            client = LLMClient(
                model=model,
                base_url=body.base_url,
                api_key=api_key,
                provider=provider_literal,  # type: ignore[arg-type]
                timeout=10.0,
            )
    except Exception as exc:  # construction / resolution failure
        logger.warning("provider_test_failed", name=body.name, error=str(exc))
        return ProviderTestResponse(ok=False, error=str(exc), model=model)

    try:
        await client.complete([{"role": "user", "content": "ping"}], max_tokens=4)
    except Exception as exc:
        logger.info("provider_test_negative", name=body.name, error=str(exc))
        return ProviderTestResponse(ok=False, error=str(exc), model=model)

    return ProviderTestResponse(ok=True, model=model)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _enforce_single_default(
    providers: list[ProviderConfig],
    updated: ProviderConfig,
) -> None:
    """If the updated provider is default, clear the flag on all others."""
    if updated.is_default:
        for p in providers:
            if p is not updated:
                p.is_default = False
