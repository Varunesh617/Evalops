"""Plugin routes — install, uninstall, browse, rate, and configure plugins."""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.plugins.discovery import PluginDiscovery
from backend.plugins.loader import PluginLoader
from backend.plugins.marketplace import PluginMarketplace
from backend.plugins.registry import PluginRegistry
from backend.plugins.security import (
    PluginSandbox,
    PluginSecurityError,
    PluginSignatureMissing,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/plugins", tags=["plugins"])

# ---------------------------------------------------------------------------
# Singleton instances — created once, served via DI providers
# ---------------------------------------------------------------------------

_registry = PluginRegistry()
_loader = PluginLoader()
_discovery = PluginDiscovery()
_marketplace = PluginMarketplace(_registry, _loader, _discovery)
_sandbox = PluginSandbox()


# ---------------------------------------------------------------------------
# DI providers (Fix C1: replaces bare module-level references in routes)
# ---------------------------------------------------------------------------


def get_plugin_registry() -> PluginRegistry:
    """FastAPI dependency — return the shared PluginRegistry singleton."""
    return _registry


def get_plugin_loader() -> PluginLoader:
    """FastAPI dependency — return the shared PluginLoader singleton."""
    return _loader


def get_plugin_marketplace() -> PluginMarketplace:
    """FastAPI dependency — return the shared PluginMarketplace singleton."""
    return _marketplace


def get_plugin_sandbox() -> PluginSandbox:
    """FastAPI dependency — return the shared PluginSandbox singleton."""
    return _sandbox


# ---------------------------------------------------------------------------
# Auth dependency (Fix C2: require API key for mutating operations)
# ---------------------------------------------------------------------------

_REQUIRED_API_KEY = os.environ.get("EVALOPS_ADMIN_API_KEY", "")


async def require_admin_auth(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """Validate the X-API-Key header against EVALOPS_ADMIN_API_KEY.

    Returns the validated key on success, raises 401 on failure.
    Only enforced when EVALOPS_ADMIN_API_KEY is configured; when the
    env var is unset the check is skipped (dev-mode convenience).
    """
    if not _REQUIRED_API_KEY:
        return "dev-mode-no-key"
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header. Plugin install/uninstall requires authentication.",
        )
    if x_api_key != _REQUIRED_API_KEY:
        logger.warning(
            "plugin_auth_failed",
            hint="Invalid API key provided for plugin mutation endpoint.",
        )
        raise HTTPException(status_code=401, detail="Invalid API key.")
    return x_api_key


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class PluginInstallRequest(BaseModel):
    plugin_id: str = Field(..., description="PyPI package name (e.g. evalops-phi-filter)")
    version: str | None = Field(default=None, description="Optional version pin")


class PluginInstallResponse(BaseModel):
    success: bool
    plugin_id: str
    message: str
    version: str = ""
    signed: bool = False


class PluginRateRequest(BaseModel):
    stars: int = Field(..., ge=1, le=5, description="Rating 1-5")


class PluginConfigResponse(BaseModel):
    plugin_id: str
    name: str
    version: str
    config_schema: dict[str, Any]


class PluginInfoResponse(BaseModel):
    plugin_id: str
    name: str
    version: str
    author: str
    description: str
    plugin_type: str
    enabled: bool = True
    rating: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)


class MarketplacePluginResponse(BaseModel):
    plugin_id: str
    name: str
    version: str
    summary: str
    author: str
    homepage: str = ""
    installed: bool = False
    installed_version: str = ""
    rating: float = 0.0
    rating_count: int = 0
    compatible: bool = True


class PluginListResponse(BaseModel):
    plugins: list[PluginInfoResponse]
    total: int


class MarketplaceListResponse(BaseModel):
    plugins: list[MarketplacePluginResponse]
    total: int


# ---------------------------------------------------------------------------
# Read-only routes (no auth required)
# ---------------------------------------------------------------------------


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    plugin_type: str | None = Query(default=None, description="Filter by type"),
    enabled_only: bool = Query(default=False),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> PluginListResponse:
    """List installed plugins."""
    if enabled_only:
        records = registry.list_enabled()
    elif plugin_type:
        records = registry.list_by_type(plugin_type)
    else:
        records = registry.list_all()

    infos = []
    for rec in records:
        rating = registry.get_rating(rec.plugin_id)
        usage = registry.get_usage_stats(rec.plugin_id)
        infos.append(PluginInfoResponse(
            plugin_id=rec.plugin_id,
            name=rec.name,
            version=rec.version,
            author=rec.author,
            description=rec.description,
            plugin_type=rec.plugin_type,
            enabled=rec.enabled,
            rating={"average": rating.average, "count": rating.count} if rating else {},
            usage=usage,
        ))
    return PluginListResponse(plugins=infos, total=len(infos))


@router.get("/marketplace", response_model=MarketplaceListResponse)
async def browse_marketplace(
    search: str | None = Query(default=None),
    marketplace: PluginMarketplace = Depends(get_plugin_marketplace),
) -> MarketplaceListResponse:
    """Browse available plugins from the marketplace."""
    listings = marketplace.list_available()
    if search:
        search_lower = search.lower()
        listings = [
            m for m in listings
            if search_lower in m.name.lower() or search_lower in m.summary.lower()
        ]
    return MarketplaceListResponse(
        plugins=[
            MarketplacePluginResponse(
                plugin_id=m.plugin_id,
                name=m.name,
                version=m.version,
                summary=m.summary,
                author=m.author,
                homepage=m.homepage,
                installed=m.installed,
                installed_version=m.installed_version,
                rating=m.rating,
                rating_count=m.rating_count,
                compatible=m.compatible,
            )
            for m in listings
        ],
        total=len(listings),
    )


@router.post("/{plugin_id}/rate", response_model=dict[str, Any])
async def rate_plugin(
    plugin_id: str,
    body: PluginRateRequest,
    marketplace: PluginMarketplace = Depends(get_plugin_marketplace),
) -> dict[str, Any]:
    """Rate a plugin 1-5 stars."""
    rating = marketplace.rate(plugin_id, body.stars)
    return {
        "plugin_id": plugin_id,
        "average": rating.average,
        "count": rating.count,
        "distribution": rating.distribution,
    }


@router.get("/{plugin_id}/config", response_model=PluginConfigResponse)
async def get_plugin_config(
    plugin_id: str,
    marketplace: PluginMarketplace = Depends(get_plugin_marketplace),
) -> PluginConfigResponse:
    """Get a plugin's configuration schema."""
    info = marketplace.get_plugin_info(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return PluginConfigResponse(
        plugin_id=info["plugin_id"],
        name=info["name"],
        version=info["version"],
        config_schema=info["config_schema"],
    )


@router.get("/{plugin_id}", response_model=PluginInfoResponse)
async def get_plugin(
    plugin_id: str,
    marketplace: PluginMarketplace = Depends(get_plugin_marketplace),
) -> PluginInfoResponse:
    """Get detailed info about an installed plugin."""
    info = marketplace.get_plugin_info(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return PluginInfoResponse(**{k: info[k] for k in PluginInfoResponse.model_fields})


# ---------------------------------------------------------------------------
# Mutating routes (auth required, security sandbox enforced)
# ---------------------------------------------------------------------------


@router.post("/install", response_model=PluginInstallResponse, status_code=201)
async def install_plugin(
    body: PluginInstallRequest,
    _auth: str = Depends(require_admin_auth),
    marketplace: PluginMarketplace = Depends(get_plugin_marketplace),
    sandbox: PluginSandbox = Depends(get_plugin_sandbox),
) -> PluginInstallResponse:
    """Install a plugin from PyPI.

    Requires X-API-Key header when EVALOPS_ADMIN_API_KEY is configured.
    Verifies plugin signing status and logs the operation for audit.
    """
    sandbox.log_operation("install_attempt", plugin_id=body.plugin_id, version=body.version)

    # Check plugin signing before installation
    signed = sandbox.check_signing(body.plugin_id)
    if not signed:
        logger.warning(
            "plugin_install_unsigned",
            plugin_id=body.plugin_id,
            hint="Installing unsigned plugin — ensure you trust this package.",
        )

    try:
        with sandbox.timed_execution(body.plugin_id):
            result = marketplace.install(body.plugin_id, version=body.version)
    except PluginSecurityError as exc:
        sandbox.log_operation("install_blocked", plugin_id=body.plugin_id, reason=str(exc))
        raise HTTPException(status_code=422, detail=str(exc))

    if not result.success:
        sandbox.log_operation("install_failed", plugin_id=body.plugin_id, message=result.message)
        raise HTTPException(status_code=422, detail=result.message)

    sandbox.log_operation("install_success", plugin_id=body.plugin_id, version=result.version)
    return PluginInstallResponse(
        success=result.success,
        plugin_id=result.plugin_id,
        message=result.message,
        version=result.version,
        signed=signed,
    )


@router.delete("/{plugin_id}", response_model=PluginInstallResponse)
async def uninstall_plugin(
    plugin_id: str,
    _auth: str = Depends(require_admin_auth),
    marketplace: PluginMarketplace = Depends(get_plugin_marketplace),
    sandbox: PluginSandbox = Depends(get_plugin_sandbox),
) -> PluginInstallResponse:
    """Uninstall a plugin.

    Requires X-API-Key header when EVALOPS_ADMIN_API_KEY is configured.
    """
    sandbox.log_operation("uninstall_attempt", plugin_id=plugin_id)
    result = marketplace.uninstall(plugin_id)
    if not result.success:
        sandbox.log_operation("uninstall_failed", plugin_id=plugin_id, message=result.message)
        raise HTTPException(status_code=404, detail=result.message)

    sandbox.log_operation("uninstall_success", plugin_id=plugin_id)
    return PluginInstallResponse(
        success=result.success,
        plugin_id=result.plugin_id,
        message=result.message,
        version=result.version,
    )
