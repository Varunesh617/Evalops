"""Plugin marketplace — browse, install, rate, and manage EvalOps plugins."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.plugins.discovery import DiscoveryCache, DiscoveredPlugin, PluginDiscovery
from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRating, PluginRecord, PluginRegistry
from backend.plugins.sdk import PluginBase

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class MarketplacePlugin:
    """A plugin listing as it appears in the marketplace."""

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
    downloads: int = 0
    tags: list[str] = field(default_factory=list)
    compatible: bool = True


@dataclass(slots=True)
class InstallResult:
    """Outcome of a plugin install/uninstall operation."""

    success: bool
    plugin_id: str
    message: str
    version: str = ""


class PluginMarketplace:
    """High-level interface for the EvalOps plugin marketplace."""

    def __init__(
        self,
        registry: PluginRegistry,
        loader: PluginLoader,
        discovery: PluginDiscovery | None = None,
    ) -> None:
        self._registry = registry
        self._loader = loader
        self._discovery = discovery or PluginDiscovery()
        self._install_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------

    def list_available(self) -> list[MarketplacePlugin]:
        """Merge installed + PyPI-discovered plugins into marketplace listings."""
        installed = {r.plugin_id: r for r in self._registry.list_all()}
        discovered = self._discovery.query_pypi()

        listings: list[MarketplacePlugin] = []

        # Installed plugins appear first
        for plugin_id, rec in installed.items():
            rating = self._registry.get_rating(plugin_id)
            listings.append(MarketplacePlugin(
                plugin_id=plugin_id,
                name=rec.name,
                version=rec.version,
                summary=rec.description,
                author=rec.author,
                installed=True,
                installed_version=rec.version,
                rating=rating.average if rating else 0.0,
                rating_count=rating.count if rating else 0,
                downloads=rec.downloads,
            ))

        # Discovered but not installed
        seen = set(installed.keys())
        for dp in discovered:
            if dp.name not in seen:
                compat = PluginDiscovery.check_compatibility(dp.requires_python)
                listings.append(MarketplacePlugin(
                    plugin_id=dp.name,
                    name=dp.name,
                    version=dp.version,
                    summary=dp.summary,
                    author=dp.author,
                    homepage=dp.homepage,
                    compatible=compat["compatible"],
                    tags=dp.classifiers,
                ))

        return listings

    def list_popular(self, limit: int = 10) -> list[MarketplacePlugin]:
        """Return the most-used plugins."""
        popular = self._registry.get_popular(limit)
        all_listings = {m.plugin_id: m for m in self.list_available()}
        return [all_listings[p.plugin_id] for p in popular if p.plugin_id in all_listings]

    # ------------------------------------------------------------------
    # Install / uninstall
    # ------------------------------------------------------------------

    def install(self, plugin_id: str, *, version: str | None = None) -> InstallResult:
        """Install a plugin by package name."""
        existing = self._registry.get(plugin_id)
        if existing is not None:
            return InstallResult(
                success=False,
                plugin_id=plugin_id,
                message=f"Plugin '{plugin_id}' is already installed (v{existing.version})",
                version=existing.version,
            )

        try:
            plugins = self._loader.load_from_pip(plugin_id)
        except Exception as exc:
            return InstallResult(
                success=False,
                plugin_id=plugin_id,
                message=f"Failed to load plugin '{plugin_id}': {exc}",
            )

        if not plugins:
            return InstallResult(
                success=False,
                plugin_id=plugin_id,
                message=f"Package '{plugin_id}' does not contain EvalOps plugins",
            )

        installed_version = ""
        for plugin in plugins.values():
            record = self._registry.register(plugin, source=f"pip:{plugin_id}")
            plugin.on_install()
            self._registry.record_download(plugin_id)
            installed_version = record.version

        self._install_log.append({
            "action": "install",
            "plugin_id": plugin_id,
            "version": installed_version,
            "timestamp": time.time(),
        })
        logger.info("marketplace_install", plugin_id=plugin_id, version=installed_version)

        return InstallResult(
            success=True,
            plugin_id=plugin_id,
            message=f"Successfully installed '{plugin_id}' v{installed_version}",
            version=installed_version,
        )

    def uninstall(self, plugin_id: str) -> InstallResult:
        """Uninstall a plugin."""
        record = self._registry.unregister(plugin_id)
        if record is None:
            return InstallResult(
                success=False,
                plugin_id=plugin_id,
                message=f"Plugin '{plugin_id}' is not installed",
            )

        self._install_log.append({
            "action": "uninstall",
            "plugin_id": plugin_id,
            "timestamp": time.time(),
        })
        logger.info("marketplace_uninstall", plugin_id=plugin_id)

        return InstallResult(
            success=True,
            plugin_id=plugin_id,
            message=f"Successfully uninstalled '{plugin_id}'",
            version=record.version,
        )

    # ------------------------------------------------------------------
    # Ratings & reviews
    # ------------------------------------------------------------------

    def rate(self, plugin_id: str, stars: int) -> PluginRating:
        """Rate a plugin 1-5 stars."""
        return self._registry.rate(plugin_id, stars)

    def get_rating(self, plugin_id: str) -> PluginRating | None:
        return self._registry.get_rating(plugin_id)

    def get_reviews_summary(self, plugin_id: str) -> dict[str, Any]:
        """Return a summary of ratings for a plugin."""
        rating = self._registry.get_rating(plugin_id)
        record = self._registry.get(plugin_id)
        if rating is None:
            return {
                "plugin_id": plugin_id,
                "average": 0.0,
                "count": 0,
                "distribution": {},
                "has_reviews": False,
            }
        return {
            "plugin_id": plugin_id,
            "average": rating.average,
            "count": rating.count,
            "distribution": rating.distribution,
            "has_reviews": rating.count > 0,
            "name": record.name if record else plugin_id,
        }

    # ------------------------------------------------------------------
    # Install history
    # ------------------------------------------------------------------

    def get_install_history(self) -> list[dict[str, Any]]:
        return list(self._install_log)

    def get_plugin_info(self, plugin_id: str) -> dict[str, Any] | None:
        """Return comprehensive info about an installed plugin."""
        record = self._registry.get(plugin_id)
        if record is None:
            return None
        rating = self._registry.get_rating(plugin_id)
        usage = self._registry.get_usage_stats(plugin_id)
        return {
            "plugin_id": record.plugin_id,
            "name": record.name,
            "version": record.version,
            "author": record.author,
            "description": record.description,
            "plugin_type": record.plugin_type,
            "fingerprint": record.fingerprint,
            "enabled": record.enabled,
            "config_schema": record.config_schema,
            "dependencies": record.dependencies,
            "installed_at": record.installed_at,
            "rating": {
                "average": rating.average if rating else 0.0,
                "count": rating.count if rating else 0,
            },
            "usage": usage,
        }
