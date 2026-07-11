"""Plugin discovery — scan entry points, query PyPI, cache results."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import structlog

logger = structlog.get_logger(__name__)

PYPI_SEARCH_URL = "https://pypi.org/pypi/{package_name}/json"
PYPI_SEARCH_PREFIX = "evalops-"
CACHE_TTL_SECONDS = 3600


@dataclass(slots=True)
class DiscoveredPlugin:
    """Lightweight description of a plugin found during discovery."""

    name: str
    version: str
    summary: str = ""
    author: str = ""
    homepage: str = ""
    requires_python: str = ""
    download_url: str = ""
    classifiers: list[str] = field(default_factory=list)


@dataclass
class DiscoveryCache:
    """Time-bounded cache for discovery results."""

    results: list[DiscoveredPlugin] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def is_valid(self) -> bool:
        return (time.time() - self.timestamp) < CACHE_TTL_SECONDS


class PluginDiscovery:
    """Scans multiple sources to find available EvalOps plugins."""

    def __init__(self) -> None:
        self._pypi_cache: DiscoveryCache = DiscoveryCache()
        self._entry_point_cache: DiscoveryCache = DiscoveryCache()

    # ------------------------------------------------------------------
    # Entry-point scanning
    # ------------------------------------------------------------------

    def scan_entry_points(self) -> list[DiscoveredPlugin]:
        """Scan the ``evalops.plugins`` entry-point group for installed plugins."""
        if self._entry_point_cache.is_valid:
            return self._entry_point_cache.results

        import importlib.metadata

        plugins: list[DiscoveredPlugin] = []
        try:
            eps = importlib.metadata.entry_points(group="evalops.plugins")
        except TypeError:
            eps = importlib.metadata.entry_points().get("evalops.plugins", [])

        for ep in eps:
            try:
                dist = ep.dist
                meta: dict[str, Any] = {}
                if dist is not None:
                    meta = {
                        "name": dist.metadata["Name"],
                        "version": dist.metadata["Version"],
                        "summary": dist.metadata.get("Summary", ""),
                        "author": dist.metadata.get("Author", ""),
                        "requires_python": dist.metadata.get("Requires-Python", ""),
                    }
                plugins.append(DiscoveredPlugin(
                    name=ep.name,
                    version=meta.get("version", "unknown"),
                    summary=meta.get("summary", ""),
                    author=meta.get("author", ""),
                    requires_python=meta.get("requires_python", ""),
                ))
            except Exception as exc:
                logger.warning("entry_point_scan_error", entry_point=ep.name, error=str(exc))

        self._entry_point_cache = DiscoveryCache(results=plugins, timestamp=time.time())
        return plugins

    # ------------------------------------------------------------------
    # PyPI querying
    # ------------------------------------------------------------------

    def query_pypi(self, *, prefix: str = PYPI_SEARCH_PREFIX) -> list[DiscoveredPlugin]:
        """Fetch known evalops-* packages from PyPI."""
        if self._pypi_cache.is_valid:
            return self._pypi_cache.results

        plugins: list[DiscoveredPlugin] = []
        for name in self._discover_pypi_packages(prefix):
            plugin = self._fetch_pypi_package(name)
            if plugin is not None:
                plugins.append(plugin)

        self._pypi_cache = DiscoveryCache(results=plugins, timestamp=time.time())
        return plugins

    def query_pypi_package(self, package_name: str) -> DiscoveredPlugin | None:
        """Fetch metadata for a single package from PyPI."""
        return self._fetch_pypi_package(package_name)

    def _discover_pypi_packages(self, prefix: str) -> list[str]:
        """Use the PyPI simple index to discover packages matching a prefix."""
        search_url = "https://pypi.org/simple/"
        try:
            request = Request(search_url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read())
            if isinstance(data, dict):
                names = data.get("projects", [])
            elif isinstance(data, list):
                names = data
            else:
                names = []
            return [
                n for n in names
                if isinstance(n, str) and n.startswith(prefix)
            ]
        except (URLError, json.JSONDecodeError, OSError) as exc:
            logger.warning("pypi_simple_index_failed", error=str(exc))
            return []

    def _fetch_pypi_package(self, package_name: str) -> DiscoveredPlugin | None:
        url = PYPI_SEARCH_URL.format(package_name=package_name)
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read())
            info = data.get("info", {})
            classifiers = info.get("classifiers", [])
            return DiscoveredPlugin(
                name=info.get("name", package_name),
                version=info.get("version", "0.0.0"),
                summary=info.get("summary", ""),
                author=info.get("author", info.get("author_email", "")),
                homepage=info.get("home_page", info.get("project_url", "")),
                requires_python=info.get("requires_python", ""),
                download_url=info.get("download_url", ""),
                classifiers=classifiers if isinstance(classifiers, list) else [],
            )
        except (URLError, json.JSONDecodeError, OSError) as exc:
            logger.warning("pypi_package_fetch_failed", package=package_name, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Version compatibility
    # ------------------------------------------------------------------

    @staticmethod
    def check_compatibility(requires_python: str) -> dict[str, Any]:
        """Check if a requires-python specifier is compatible with the running interpreter."""
        import sys

        from packaging.specifiers import SpecifierSet

        result = {"compatible": True, "python_version": f"{sys.version_info.major}.{sys.version_info.minor}"}
        if not requires_python:
            return result
        try:
            spec = SpecifierSet(requires_python)
            current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            result["compatible"] = current in spec
            result["requires_python"] = requires_python
        except Exception:
            result["compatible"] = True
            result["requires_python"] = requires_python
        return result

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate_cache(self) -> None:
        self._pypi_cache = DiscoveryCache()
        self._entry_point_cache = DiscoveryCache()
