"""Plugin routes — install, uninstall, browse, rate, and configure plugins."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.plugins.discovery import PluginDiscovery
from backend.plugins.loader import PluginLoader
from backend.plugins.marketplace import PluginMarketplace
from backend.plugins.registry import PluginRegistry

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/plugins", tags=["plugins"])

# ---------------------------------------------------------------------------
# Singletons (module-level for the in-memory demo)
# ---------------------------------------------------------------------------

_registry = PluginRegistry()
_loader = PluginLoader()
_discovery = PluginDiscovery()
_marketplace = PluginMarketplace(_registry, _loader, _discovery)


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
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    plugin_type: str | None = Query(default=None, description="Filter by type"),
    enabled_only: bool = Query(default=False),
) -> PluginListResponse:
    """List installed plugins."""
    if enabled_only:
        records = _registry.list_enabled()
    elif plugin_type:
        records = _registry.list_by_type(plugin_type)
    else:
        records = _registry.list_all()

    infos = []
    for rec in records:
        rating = _registry.get_rating(rec.plugin_id)
        usage = _registry.get_usage_stats(rec.plugin_id)
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


@router.post("/install", response_model=PluginInstallResponse, status_code=201)
async def install_plugin(body: PluginInstallRequest) -> PluginInstallResponse:
    """Install a plugin from PyPI."""
    result = _marketplace.install(body.plugin_id, version=body.version)
    if not result.success:
        raise HTTPException(status_code=422, detail=result.message)
    return PluginInstallResponse(
        success=result.success,
        plugin_id=result.plugin_id,
        message=result.message,
        version=result.version,
    )


@router.delete("/{plugin_id}", response_model=PluginInstallResponse)
async def uninstall_plugin(plugin_id: str) -> PluginInstallResponse:
    """Uninstall a plugin."""
    result = _marketplace.uninstall(plugin_id)
    if not result.success:
        raise HTTPException(status_code=404, detail=result.message)
    return PluginInstallResponse(
        success=result.success,
        plugin_id=result.plugin_id,
        message=result.message,
        version=result.version,
    )


@router.get("/marketplace", response_model=MarketplaceListResponse)
async def browse_marketplace(
    search: str | None = Query(default=None),
) -> MarketplaceListResponse:
    """Browse available plugins from the marketplace."""
    listings = _marketplace.list_available()
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
async def rate_plugin(plugin_id: str, body: PluginRateRequest) -> dict[str, Any]:
    """Rate a plugin 1-5 stars."""
    rating = _marketplace.rate(plugin_id, body.stars)
    return {
        "plugin_id": plugin_id,
        "average": rating.average,
        "count": rating.count,
        "distribution": rating.distribution,
    }


@router.get("/{plugin_id}/config", response_model=PluginConfigResponse)
async def get_plugin_config(plugin_id: str) -> PluginConfigResponse:
    """Get a plugin's configuration schema."""
    info = _marketplace.get_plugin_info(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return PluginConfigResponse(
        plugin_id=info["plugin_id"],
        name=info["name"],
        version=info["version"],
        config_schema=info["config_schema"],
    )


@router.get("/{plugin_id}", response_model=PluginInfoResponse)
async def get_plugin(plugin_id: str) -> PluginInfoResponse:
    """Get detailed info about an installed plugin."""
    info = _marketplace.get_plugin_info(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return PluginInfoResponse(**{k: info[k] for k in PluginInfoResponse.model_fields})
