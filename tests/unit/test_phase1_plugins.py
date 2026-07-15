"""Phase 1 tests — ConfigSweeper user_attrs, plugin lifecycle hooks,
DB persistence, PyPI discovery HTML parsing, and restricted builtins.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.config import PipelineConfig
from backend.optimizer.config_sweeper import (
    ConfigSweeper,
    EvalOutcome,
    SweepResult,
)
from backend.plugins.discovery import PluginDiscovery, _SimpleIndexParser
from backend.plugins.loader import PluginLoader, PluginLoadError
from backend.plugins.marketplace import PluginMarketplace
from backend.plugins.registry import PluginRegistry
from backend.plugins.sdk import PluginBase
from backend.plugins.security import PluginSandbox, PluginSecurityError


# ---------------------------------------------------------------------------
# 1.1 — ConfigSweeper user_attrs
# ---------------------------------------------------------------------------


class _FakePlugin(PluginBase):
    plugin_id = "fake-metric"
    name = "Fake Metric"
    version = "1.0.0"
    author = "tester"
    description = "Fake metric plugin for tests"

    def config_schema(self) -> dict:
        return {}


class TestConfigSweeperUserAttrs:
    @pytest.mark.anyio
    async def test_user_attrs_populated(self):
        async def eval_fn(config: PipelineConfig) -> EvalOutcome:
            return EvalOutcome(quality_score=0.9, cost_usd=0.04, latency_ms=120.0)

        # optuna's param-importance step needs sklearn; stub it so the test
        # isolates the user_attrs behaviour added in 1.1.
        with patch("optuna.importance.get_param_importances", return_value={}):
            sweeper = ConfigSweeper(eval_fn=eval_fn, n_trials=3)
            result = await sweeper.run()

        assert isinstance(result, SweepResult)
        assert result.best_quality_score > 0
        assert result.best_cost_usd > 0
        assert result.best_latency_ms >= 0
        for t in result.all_trials:
            assert t.quality_score > 0
            assert t.cost_usd > 0


# ---------------------------------------------------------------------------
# 1.5 — PyPI discovery HTML parsing
# ---------------------------------------------------------------------------


_SIMPLE_HTML = """
<!DOCTYPE html>
<html><body>
<a href="https://pypi.org/simple/evalops-foo/">evalops-foo</a>
<a href="https://pypi.org/simple/evalops-bar/">evalops-bar</a>
<a href="https://pypi.org/simple/requests/">requests</a>
<a href="https://pypi.org/simple/evalops-baz/">evalops-baz</a>
</body></html>
"""


class TestSimpleIndexParser:
    def test_parses_anchor_hrefs(self):
        parser = _SimpleIndexParser()
        parser.feed(_SIMPLE_HTML)
        assert set(parser.names) == {"evalops-foo", "evalops-bar", "requests", "evalops-baz"}

    def test_discover_filters_by_prefix(self):
        discovery = PluginDiscovery()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _SIMPLE_HTML.encode("utf-8")

        with patch("backend.plugins.discovery.urlopen", return_value=_Resp()):
            names = discovery._discover_pypi_packages("evalops-")
        assert names == ["evalops-foo", "evalops-bar", "evalops-baz"]

    def test_json_body_no_longer_breaks_discovery(self):
        discovery = PluginDiscovery()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"projects": [1, 2, 3]}'

        with patch("backend.plugins.discovery.urlopen", return_value=_Resp()):
            names = discovery._discover_pypi_packages("evalops-")
        assert names == []


# ---------------------------------------------------------------------------
# 1.2 — Plugin lifecycle hooks (in-memory; no DB)
# ---------------------------------------------------------------------------


class _HookPlugin(PluginBase):
    plugin_id = "hook-plugin"
    name = "Hook Plugin"
    version = "1.0.0"

    def __init__(self) -> None:
        self.enabled_called = False
        self.disabled_called = False
        self.uninstalled_called = False

    def config_schema(self) -> dict:
        return {}

    def on_enable(self) -> None:
        self.enabled_called = True

    def on_disable(self) -> None:
        self.disabled_called = True

    def on_uninstall(self) -> None:
        self.uninstalled_called = True


class TestPluginLifecycleHooks:
    @pytest.mark.anyio
    async def test_set_enabled_fires_hook(self):
        registry = PluginRegistry()
        plugin = _HookPlugin()
        await registry.register(plugin, source="test")
        assert await registry.set_enabled("hook-plugin", False, plugin=plugin) is True
        assert plugin.disabled_called is True
        assert plugin.enabled_called is False

        assert await registry.set_enabled("hook-plugin", True, plugin=plugin) is True
        assert plugin.enabled_called is True

    @pytest.mark.anyio
    async def test_hook_failure_does_not_break_toggle(self):
        registry = PluginRegistry()
        plugin = _HookPlugin()
        await registry.register(plugin, source="test")

        def boom():
            raise RuntimeError("hook broke")

        plugin.on_disable = boom  # type: ignore[assignment]
        assert await registry.set_enabled("hook-plugin", False, plugin=plugin) is True

    @pytest.mark.anyio
    async def test_uninstall_fires_on_uninstall(self):
        registry = PluginRegistry()
        loader = PluginLoader()
        marketplace = PluginMarketplace(registry, loader)
        plugin = _HookPlugin()
        await registry.register(plugin, source="pip:hook-plugin")
        marketplace._installed["hook-plugin"] = plugin

        result = await marketplace.uninstall("hook-plugin")
        assert result.success is True
        assert plugin.uninstalled_called is True
        assert marketplace._installed.get("hook-plugin") is None


# ---------------------------------------------------------------------------
# 1.6 — Restricted builtins injection
# ---------------------------------------------------------------------------


def _write_module(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    return path


class TestRestrictedBuiltins:
    def test_restricted_builtins_blocks_open(self, tmp_path):
        path = _write_module(
            tmp_path,
            "evil",
            "data = open('/etc/passwd').read()\n",
        )
        loader = PluginLoader(sandbox=PluginSandbox())
        # The module-level open() is stripped from the restricted builtins,
        # so execution fails (and is surfaced by the loader as PluginLoadError).
        with pytest.raises((PluginLoadError, PluginSecurityError, NameError)):
            loader._load_module_from_path(path)

    def test_no_sandbox_loads_fine(self, tmp_path):
        path = _write_module(
            tmp_path,
            "ok",
            "value = 41 + 1\n",
        )
        loader = PluginLoader(sandbox=None)
        plugins = loader.load_from_directory(tmp_path)
        assert len(plugins) == 0

    def test_import_still_works_under_sandbox(self, tmp_path):
        # __import__ is re-injected into the restricted builtins, so normal
        # imports inside plugin module code still succeed.
        path = _write_module(
            tmp_path,
            "uses_import",
            "import json\n"
            "payload = json.dumps({'a': 1})\n",
        )
        loader = PluginLoader(sandbox=PluginSandbox())
        # No PluginBase subclass here, so it loads without error but yields none.
        plugins = loader.load_from_directory(tmp_path)
        assert plugins == {}
