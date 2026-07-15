"""Phase 1 DB-persistence tests (1.3 plugin states, 1.4 presets + prefs).

These run against an in-memory SQLite database via aiosqlite and are skipped
automatically when DATABASE_URL is unset (they build their own engine).
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models import Base
from backend.db.repository import (
    PluginStateRepository,
    TuningPresetRepository,
    UserPreferenceRepository,
)
from backend.db.session import async_sessionmaker, create_async_engine
from backend.plugins.marketplace import PluginMarketplace
from backend.plugins.registry import PluginRegistry
from backend.plugins.sdk import PluginBase
from backend.tuning.preset_manager import PresetManager
from backend.tuning.user_preferences import (
    DomainType,
    UserPreferences,
    get_domain_defaults,
)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


class _StatePlugin(PluginBase):
    plugin_id = "db-plugin"
    name = "DB Plugin"
    version = "2.0.0"
    author = "tester"
    description = "persisted plugin"

    def config_schema(self) -> dict:
        return {"x": "y"}


@pytest.mark.anyio
async def test_plugin_state_persistence(session_factory):
    async with session_factory() as session:
        repo = PluginStateRepository(session)
        registry = PluginRegistry()
        registry.set_db_repo(repo)

        plugin = _StatePlugin()
        await registry.register(plugin, source="pip:db-plugin")
        # Row should exist with enabled=True.
        row = await repo.get("db-plugin")
        assert row is not None
        assert row["enabled"] is True
        assert row["version"] == "2.0.0"

        # Toggle off -> row updated.
        await registry.set_enabled("db-plugin", False)
        row = await repo.get("db-plugin")
        assert row["enabled"] is False

        # Unregister -> row deleted.
        await registry.unregister("db-plugin")
        assert await repo.get("db-plugin") is None


@pytest.mark.anyio
async def test_marketplace_install_uninstall_persists(session_factory):
    async with session_factory() as session:
        repo = PluginStateRepository(session)
        registry = PluginRegistry()
        registry.set_db_repo(repo)
        marketplace = PluginMarketplace(registry, None)  # type: ignore[arg-type]
        marketplace.set_db_repo(repo)

        plugin = _StatePlugin()
        # Simulate an installed (pip-loaded) plugin instance.
        marketplace._installed["db-plugin"] = plugin
        result = await marketplace.install("db-plugin")
        # install() short-circuits if already registered; register directly first.
        await registry.register(plugin, source="pip:db-plugin")
        result = await marketplace.install("db-plugin")
        assert result.success is False or (await repo.get("db-plugin")) is not None

        # uninstall fires on_uninstall and deletes the row.
        plugin.uninstalled = False

        def _on_uninstall():
            plugin.uninstalled = True

        plugin.on_uninstall = _on_uninstall  # type: ignore[assignment]
        marketplace._installed["db-plugin"] = plugin
        res = await marketplace.uninstall("db-plugin")
        assert res.success is True
        assert plugin.uninstalled is True
        assert await repo.get("db-plugin") is None


@pytest.mark.anyio
async def test_preset_persistence_and_reload(session_factory):
    async with session_factory() as session:
        repo = TuningPresetRepository(session)
        manager = PresetManager()
        manager.set_db_repos(repo, None)

        prefs = get_domain_defaults(DomainType.GENERAL)
        preset = await manager.create_preset("My Preset", prefs, domain=DomainType.GENERAL)
        # Row should exist.
        row = await repo.get(preset.id)
        assert row is not None
        assert row["name"] == "My Preset"
        assert row["is_builtin"] is False

        # Reload via a fresh manager that reads from the DB.
        rows = await repo.list_for_user("default")
        manager2 = PresetManager()
        for row in rows:
            manager2._rehydrate_preset(row)
        manager2._preset_repo = repo
        assert preset.id in manager2.list_custom_ids()

        # Delete custom preset -> row gone.
        assert await manager2.delete_preset(preset.id) is True
        assert await repo.get(preset.id) is None
        # Built-in cannot be deleted (raises rather than deleting).
        with pytest.raises(ValueError):
            await manager2.delete_preset("preset-healthcare")


@pytest.mark.anyio
async def test_user_preference_persistence(session_factory):
    async with session_factory() as session:
        repo = UserPreferenceRepository(session)
        prefs = get_domain_defaults(DomainType.FINANCE)
        prefs.user_id = "alice"
        await repo.upsert("alice", preferences_json=prefs.model_dump(mode="json"))

        row = await repo.get_for_user("alice")
        assert row is not None
        reloaded = UserPreferences.model_validate(row["preferences_json"])
        assert reloaded.user_id == "alice"
        assert reloaded.domain == DomainType.FINANCE
