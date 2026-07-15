"""Plugin marketplace — browse, install, rate, and manage EvalOps plugins."""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

# Strict allowlist for package names passed to pip as argv.  Only simple PEP 508
# names with an ASCII letter/digit start and no shell/flag metacharacters.
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

from backend.plugins.async_utils import maybe_await
from backend.plugins.discovery import PluginDiscovery
from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRating, PluginRegistry

if TYPE_CHECKING:
    from backend.db.repository import PluginStateRepository

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
        self._installed: dict[str, Any] = {}
        self._instances: dict[str, Any] = {}
        self._db_repo: PluginStateRepository | None = None

    def set_db_repo(self, repo: PluginStateRepository | None) -> None:
        """Optionally wire a DB repository so install/uninstall state persists."""
        self._db_repo = repo

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

    async def install(self, plugin_id: str, *, version: str | None = None) -> InstallResult:
        """Install a plugin by package name."""
        existing = self._registry.get(plugin_id)
        if existing is not None:
            return InstallResult(
                success=False,
                plugin_id=plugin_id,
                message=f"Plugin '{plugin_id}' is already installed (v{existing.version})",
                version=existing.version,
            )

        # Real pip install (4.2): only when the package is not already importable.
        if not self._is_importable(plugin_id):
            ok, err = await self._pip_install(plugin_id, version)
            if not ok:
                return InstallResult(
                    success=False,
                    plugin_id=plugin_id,
                    message=f"pip install failed: {err}",
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

        # CRITICAL 3: verify the plugin signature AFTER loading the module but
        # BEFORE registering it.  When EVALOPS_REQUIRE_SIGNED is enabled, an
        # unsigned/unverifiable plugin aborts the install (no registration).
        if self._loader._sandbox is not None:
            try:
                self._loader._enforce_signing(plugin_id)
            except Exception as exc:  # noqa: BLE001 - surface as install failure
                return InstallResult(
                    success=False,
                    plugin_id=plugin_id,
                    message=f"Plugin signature verification failed: {exc}",
                )

        installed_version = ""
        for plugin in plugins.values():
            record = await self._registry.register(plugin, source=f"pip:{plugin_id}")
            # Install declared dependencies (4.3) before firing lifecycle hooks.
            # Dependency names are validated and pinned to the declared version
            # so unpinned/arbitrary specs cannot be smuggled in.
            for dep in getattr(plugin, "dependencies", []) or []:
                dep_name, _, dep_ver = dep.partition("==")
                ok, err = await self._pip_install(dep_name.strip(), dep_ver.strip() or None)
                if not ok:
                    logger.warning(
                        "plugin_dependency_install_failed",
                        plugin_id=plugin.plugin_id,
                        dependency=dep,
                        error=err,
                    )
            await maybe_await(plugin.on_install_async())
            await self._registry.record_download(record.plugin_id)
            self._installed[record.plugin_id] = plugin
            self._instances[record.plugin_id] = plugin
            installed_version = record.version
            # Enable the plugin so the on_enable hook fires once (4.7).
            await self._registry.set_enabled(record.plugin_id, True, plugin=plugin)

        self._install_log.append({
            "action": "install",
            "plugin_id": plugin_id,
            "version": installed_version,
            "timestamp": time.time(),
        })
        logger.info("marketplace_install", plugin_id=plugin_id, version=installed_version)

        if self._db_repo is not None:
            try:
                await self._db_repo.upsert(
                    plugin_id,
                    name=record.name,
                    version=record.version,
                    author=record.author,
                    description=record.description,
                    plugin_type=record.plugin_type,
                    enabled=True,
                )
            except Exception as exc:
                logger.warning(
                    "plugin_state_persist_failed", plugin_id=plugin_id, error=str(exc)
                )

        return InstallResult(
            success=True,
            plugin_id=plugin_id,
            message=f"Successfully installed '{plugin_id}' v{installed_version}",
            version=installed_version,
        )

    @staticmethod
    def _is_importable(package_name: str) -> bool:
        """Return True if *package_name* can already be imported."""
        try:
            importlib.import_module(package_name)
            return True
        except ImportError:
            return False

    @staticmethod
    def _validate_package_name(package_name: str) -> None:
        """Reject package names containing shell/flag metacharacters.

        Pip parses extra argv tokens as flags/specs (``-r /etc/passwd``,
        PEP508 options), so any name not matching the strict allowlist is
        rejected before reaching the subprocess.  The name is always passed as
        a single argv element (never a shell string).
        """
        if not package_name or not _PACKAGE_NAME_RE.match(package_name):
            raise ValueError(
                f"Invalid package name '{package_name}'. Package names must "
                f"match ^[A-Za-z0-9][A-Za-z0-9._-]*$ (no spaces, '/', ';', "
                f"'=', ':' or leading '-')."
            )

    async def _pip_install(
        self, package_name: str, version: str | None = None
    ) -> tuple[bool, str]:
        """Install *package_name* via pip in a subprocess (never a shell).

        Uses a list of arguments and ``asyncio.create_subprocess_exec`` so no
        shell interpolation can occur (no shell-injection surface).  The package
        name is validated against a strict allowlist and, when a version is
        supplied, pinned with ``==version`` so unpinned/arbitrary specs cannot
        be installed.  Enforces a 300s timeout and captures stderr.
        """
        self._validate_package_name(package_name)
        if version is not None:
            self._validate_package_name(version)

        args = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-input",
        ]
        spec = f"{package_name}=={version}" if version else package_name
        args.append(spec)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            logger.warning("plugin_pip_install_timeout", package=package_name)
            return False, f"pip install timed out after 300s for '{package_name}'"
        except Exception as exc:
            logger.warning(
                "plugin_pip_install_error", package=package_name, error=str(exc)
            )
            return False, f"pip install failed for '{package_name}': {exc}"

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", "replace")[:500]
            logger.warning(
                "plugin_pip_install_failed",
                package=package_name,
                returncode=proc.returncode,
                stderr=err,
            )
            return False, err or f"pip install failed (rc={proc.returncode})"
        logger.info("plugin_pip_install_ok", package=package_name)
        return True, (stdout or b"").decode("utf-8", "replace")[:500]

    async def uninstall(self, plugin_id: str) -> InstallResult:
        """Uninstall a plugin."""
        # Prefer the instance map so on_uninstall fires even for sources that
        # were never stashed in the older ``_installed`` dict (4.7).
        plugin = self._instances.get(plugin_id) or self._installed.get(plugin_id)
        if plugin is not None:
            try:
                await maybe_await(plugin.on_uninstall_async())
            except Exception as exc:
                logger.warning(
                    "plugin_lifecycle_hook_failed",
                    plugin_id=plugin_id,
                    hook="on_uninstall",
                    error=str(exc),
                )
            self._installed.pop(plugin_id, None)
            self._instances.pop(plugin_id, None)

        record = await self._registry.unregister(plugin_id)
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

        if self._db_repo is not None:
            try:
                await self._db_repo.delete(plugin_id)
            except Exception as exc:
                logger.warning(
                    "plugin_state_delete_failed", plugin_id=plugin_id, error=str(exc)
                )

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
