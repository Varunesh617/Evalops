"""Plugin registry — install, query, and track EvalOps plugins."""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from backend.plugins.sdk import PluginBase

if TYPE_CHECKING:
    from backend.db.repository import PluginStateRepository

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class PluginRecord:
    """Metadata for a single registered plugin."""

    plugin_id: str
    name: str
    version: str
    author: str
    description: str
    plugin_type: str
    entry_point: str = ""
    config_schema: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    installed_at: float = field(default_factory=time.time)
    enabled: bool = True
    downloads: int = 0
    usage_count: int = 0
    last_used: float = 0.0

    @property
    def fingerprint(self) -> str:
        raw = f"{self.plugin_id}:{self.version}:{self.entry_point}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(slots=True)
class PluginRating:
    """Aggregate rating for a plugin."""

    plugin_id: str
    average: float = 0.0
    count: int = 0
    distribution: dict[int, int] = field(default_factory=lambda: dict.fromkeys(range(1, 6), 0))


class PluginRegistry:
    """Central registry that tracks all known EvalOps plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginRecord] = {}
        self._ratings: dict[str, PluginRating] = {}
        self._usage_log: deque[dict[str, Any]] = deque(maxlen=1000)
        self._db_repo: PluginStateRepository | None = None

    def set_db_repo(self, repo: PluginStateRepository | None) -> None:
        """Optionally wire a DB repository so state is mirrored to the database.

        When ``None`` (or ``DATABASE_URL`` is unset) the registry stays purely
        in-memory.  All DB writes are best-effort and never raise past the caller.
        """
        self._db_repo = repo

    # ------------------------------------------------------------------
    # Register / unregister
    # ------------------------------------------------------------------

    async def register(self, plugin: PluginBase, *, source: str = "unknown") -> PluginRecord:
        """Register a PluginBase instance and return its record."""
        plugin_type = type(plugin).__name__
        dependencies = list(getattr(plugin, "dependencies", []) or [])
        record = PluginRecord(
            plugin_id=plugin.plugin_id,
            name=plugin.name,
            version=plugin.version,
            author=plugin.author,
            description=plugin.description,
            plugin_type=plugin_type,
            entry_point=source,
            config_schema=plugin.config_schema(),
            dependencies=dependencies,
        )
        existing = self._plugins.get(plugin.plugin_id)
        if existing is not None:
            logger.info(
                "plugin_reregistered",
                plugin_id=plugin.plugin_id,
                old_version=existing.version,
                new_version=record.version,
            )
        self._plugins[plugin.plugin_id] = record
        logger.info("plugin_registered", plugin_id=plugin.plugin_id, version=record.version)

        if self._db_repo is not None:
            try:
                await self._db_repo.upsert(
                    record.plugin_id,
                    name=record.name,
                    version=record.version,
                    author=record.author,
                    description=record.description,
                    plugin_type=record.plugin_type,
                    entry_point=record.entry_point,
                    config_schema=record.config_schema,
                    dependencies=record.dependencies,
                    downloads=record.downloads,
                    usage_count=record.usage_count,
                    last_used=record.last_used,
                    enabled=record.enabled,
                )
            except Exception as exc:
                logger.warning(
                    "plugin_state_persist_failed", plugin_id=record.plugin_id, error=str(exc)
                )
        return record

    async def unregister(self, plugin_id: str) -> PluginRecord | None:
        """Remove a plugin from the registry. Returns the removed record or None."""
        record = self._plugins.pop(plugin_id, None)
        if record:
            logger.info("plugin_unregistered", plugin_id=plugin_id)
            if self._db_repo is not None:
                try:
                    await self._db_repo.delete(plugin_id)
                except Exception as exc:
                    logger.warning(
                        "plugin_state_delete_failed", plugin_id=plugin_id, error=str(exc)
                    )
        return record

    def _rehydrate(self, record_dict: dict[str, Any]) -> PluginRecord:
        """Rebuild a PluginRecord from a DB dict without touching the DB again."""
        record = PluginRecord(
            plugin_id=record_dict["plugin_id"],
            name=record_dict.get("name", ""),
            version=record_dict.get("version", "0.0.0"),
            author=record_dict.get("author", ""),
            description=record_dict.get("description", ""),
            plugin_type=record_dict.get("plugin_type", "plugin"),
            entry_point=record_dict.get("entry_point", ""),
            config_schema=record_dict.get("config_schema", {}),
            dependencies=record_dict.get("dependencies", []),
            installed_at=record_dict.get("installed_at", 0.0),
            enabled=record_dict.get("enabled", True),
            downloads=record_dict.get("downloads", 0),
            usage_count=record_dict.get("usage_count", 0),
            last_used=record_dict.get("last_used", 0.0),
        )
        self._plugins[record.plugin_id] = record
        return record

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._plugins.get(plugin_id)

    def list_all(self) -> list[PluginRecord]:
        return list(self._plugins.values())

    def list_by_type(self, plugin_type: str) -> list[PluginRecord]:
        return [r for r in self._plugins.values() if r.plugin_type == plugin_type]

    def list_enabled(self) -> list[PluginRecord]:
        return [r for r in self._plugins.values() if r.enabled]

    async def set_enabled(
        self,
        plugin_id: str,
        enabled: bool,
        *,
        plugin: PluginBase | None = None,
    ) -> bool:
        """Enable or disable a plugin. Fires the plugin's lifecycle hook.

        The optional ``plugin`` instance is used to call ``on_enable`` /
        ``on_disable``.  Hook failures are logged and never break the toggle.
        """
        record = self._plugins.get(plugin_id)
        if record is None:
            return False
        record.enabled = enabled
        logger.info("plugin_toggled", plugin_id=plugin_id, enabled=enabled)

        if plugin is not None:
            try:
                if enabled:
                    plugin.on_enable()
                else:
                    plugin.on_disable()
            except Exception as exc:
                logger.warning(
                    "plugin_lifecycle_hook_failed",
                    plugin_id=plugin_id,
                    enabled=enabled,
                    error=str(exc),
                )

        if self._db_repo is not None:
            try:
                await self._db_repo.upsert(plugin_id, enabled=enabled)
            except Exception as exc:
                logger.warning(
                    "plugin_state_persist_failed", plugin_id=plugin_id, error=str(exc)
                )
        return True

    # ------------------------------------------------------------------
    # Version tracking
    # ------------------------------------------------------------------

    def check_version_conflicts(self) -> list[dict[str, Any]]:
        """Return a list of plugins whose version might conflict with deps."""
        conflicts: list[dict[str, Any]] = []
        by_name: dict[str, list[PluginRecord]] = {}
        for record in self._plugins.values():
            by_name.setdefault(record.name, []).append(record)
        for name, records in by_name.items():
            if len(records) > 1:
                versions = sorted(set(r.version for r in records))
                conflicts.append({
                    "name": name,
                    "versions": versions,
                    "plugin_ids": [r.plugin_id for r in records],
                })
        return conflicts

    def resolve_dependencies(self, plugin_id: str) -> list[dict[str, Any]]:
        """Check *plugin_id*'s declared dependencies against installed records.

        Returns a structured list of conflicts; an empty list means the
        dependency set is compatible.  Reuses :meth:`check_version_conflicts`
        over a merged view of the plugin's deps plus currently-installed records.
        """
        record = self._plugins.get(plugin_id)
        if record is None:
            return [{"plugin_id": plugin_id, "error": "not_registered"}]

        conflicts: list[dict[str, Any]] = []
        for dep in record.dependencies:
            dep_name = dep.split("==")[0].strip()
            if not dep_name:
                continue
            matching = [
                r for r in self._plugins.values()
                if r.name == dep_name or r.plugin_id == dep_name
            ]
            if not matching:
                conflicts.append({
                    "dependency": dep,
                    "status": "unmet",
                    "plugin_ids": [],
                })
                continue
            # If a specific version was pinned, ensure it is satisfied.
            if "==" in dep:
                pinned = dep.split("==", 1)[1].strip()
                versions = sorted({r.version for r in matching})
                if pinned not in versions:
                    conflicts.append({
                        "dependency": dep,
                        "status": "version_mismatch",
                        "installed": versions,
                        "plugin_ids": [r.plugin_id for r in matching],
                    })
        # Also surface any internal name collisions among installed records.
        conflicts.extend(self.check_version_conflicts())
        return conflicts

    def check_updates(self) -> list[dict[str, Any]]:
        """Stub — returns plugins that have newer versions available."""
        updates: list[dict[str, Any]] = []
        for record in self._plugins.values():
            if record.version.startswith("0."):
                updates.append({
                    "plugin_id": record.plugin_id,
                    "current_version": record.version,
                    "available_version": "1.0.0",
                })
        return updates

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    async def record_usage(self, plugin_id: str) -> None:
        record = self._plugins.get(plugin_id)
        if record is None:
            return
        record.usage_count += 1
        record.last_used = time.time()
        self._usage_log.append({
            "plugin_id": plugin_id,
            "timestamp": time.time(),
        })
        if self._db_repo is not None:
            try:
                await self._db_repo.upsert(
                    plugin_id,
                    usage_count=record.usage_count,
                    last_used=record.last_used,
                )
            except Exception as exc:
                logger.warning(
                    "plugin_state_persist_failed", plugin_id=plugin_id, error=str(exc)
                )

    async def record_download(self, plugin_id: str) -> None:
        record = self._plugins.get(plugin_id)
        if record:
            record.downloads += 1
            if self._db_repo is not None:
                try:
                    await self._db_repo.upsert(plugin_id, downloads=record.downloads)
                except Exception as exc:
                    logger.warning(
                        "plugin_state_persist_failed", plugin_id=plugin_id, error=str(exc)
                    )

    def get_usage_stats(self, plugin_id: str) -> dict[str, Any]:
        record = self._plugins.get(plugin_id)
        if record is None:
            return {}
        recent = [e for e in self._usage_log if e["plugin_id"] == plugin_id]
        return {
            "plugin_id": plugin_id,
            "total_usage": record.usage_count,
            "total_downloads": record.downloads,
            "last_used": record.last_used,
            "recent_sessions": len(recent),
        }

    def get_popular(self, limit: int = 10) -> list[PluginRecord]:
        return sorted(
            self._plugins.values(),
            key=lambda r: r.usage_count + r.downloads,
            reverse=True,
        )[:limit]

    # ------------------------------------------------------------------
    # Ratings
    # ------------------------------------------------------------------

    def rate(self, plugin_id: str, stars: int) -> PluginRating:
        if not 1 <= stars <= 5:
            raise ValueError("Rating must be between 1 and 5")
        rating = self._ratings.get(plugin_id)
        if rating is None:
            rating = PluginRating(plugin_id=plugin_id)
            self._ratings[plugin_id] = rating
        rating.distribution[stars] += 1
        rating.count += 1
        total = sum(s * c for s, c in rating.distribution.items())
        rating.average = round(total / rating.count, 2)
        logger.info("plugin_rated", plugin_id=plugin_id, stars=stars, new_average=rating.average)
        return rating

    def get_rating(self, plugin_id: str) -> PluginRating | None:
        return self._ratings.get(plugin_id)

    def get_all_ratings(self) -> dict[str, PluginRating]:
        return dict(self._ratings)
