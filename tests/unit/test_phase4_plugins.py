"""Phase 4 tests — async SDK, real pip install, dependency resolution,
AST import scan, ed25519 signing, resource limits, hook gaps, pack unpacking.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.config import PluginSecurityConfig
from backend.plugins.async_utils import maybe_await
from backend.plugins.loader import PluginLoader
from backend.plugins.marketplace import PluginMarketplace
from backend.plugins.registry import PluginRegistry
from backend.plugins.sdk import PluginBase
from backend.plugins.security import PluginSandbox, PluginSecurityError


# ---------------------------------------------------------------------------
# 4.1 — async SDK variants
# ---------------------------------------------------------------------------


class _AsyncPlugin(PluginBase):
    plugin_id = "async-plugin"
    name = "Async Plugin"
    version = "1.0.0"
    author = "tester"
    description = "plugin for async SDK tests"

    def __init__(self) -> None:
        self.install_called = False
        self.uninstall_called = False

    def config_schema(self) -> dict:
        return {}

    def on_install(self) -> None:
        self.install_called = True

    def on_uninstall(self) -> None:
        self.uninstall_called = True


class TestAsyncSDK:
    @pytest.mark.anyio
    async def test_maybe_await_passthrough_and_await(self):
        assert await maybe_await(5) == 5
        assert await maybe_await(await _coro(7)) == 7

    @pytest.mark.anyio
    async def test_lifecycle_async_twins(self):
        plugin = _AsyncPlugin()
        await plugin.on_install_async()
        await plugin.on_uninstall_async()
        assert plugin.install_called is True
        assert plugin.uninstall_called is True


async def _coro(v: int) -> int:
    return v


# ---------------------------------------------------------------------------
# 4.2 — real pip install (subprocess, no shell)
# ---------------------------------------------------------------------------


class TestPipInstall:
    @pytest.mark.anyio
    async def test_pip_install_success_rc0(self):
        marketplace = PluginMarketplace(PluginRegistry(), PluginLoader())
        fake = AsyncMock()
        fake.returncode = 0
        fake.communicate.return_value = (b"done", b"")
        with patch("asyncio.create_subprocess_exec", return_value=fake) as sp:
            ok, msg = await marketplace._pip_install("some-pkg")
        assert ok is True
        called_args = sp.call_args.args
        assert called_args[0] == sys.executable
        assert called_args[1:] == ("-m", "pip", "install", "--no-input", "some-pkg")

    @pytest.mark.anyio
    async def test_pip_install_failure_rc1(self):
        marketplace = PluginMarketplace(PluginRegistry(), PluginLoader())
        fake = AsyncMock()
        fake.returncode = 1
        fake.communicate.return_value = (b"", b"requirement not found")
        with patch("asyncio.create_subprocess_exec", return_value=fake):
            ok, msg = await marketplace._pip_install("bad-pkg")
        assert ok is False
        assert "requirement not found" in msg

    @pytest.mark.anyio
    async def test_pip_install_no_shell_injection(self):
        marketplace = PluginMarketplace(PluginRegistry(), PluginLoader())
        fake = AsyncMock()
        fake.returncode = 0
        fake.communicate.return_value = (b"", b"")
        # Malicious package names containing shell/flag metacharacters are
        # rejected up-front by strict name validation (CRITICAL 2), so they
        # never reach the subprocess at all.
        with pytest.raises(ValueError):
            await marketplace._pip_install("evil; rm -rf /")

    @pytest.mark.anyio
    async def test_pip_install_valid_name_single_argv(self):
        # A legitimately-shaped name is passed through as a single argv element
        # (no shell interpolation), and the whole call is a list, never a string.
        marketplace = PluginMarketplace(PluginRegistry(), PluginLoader())
        fake = AsyncMock()
        fake.returncode = 0
        fake.communicate.return_value = (b"", b"")
        with patch("asyncio.create_subprocess_exec", return_value=fake) as sp:
            await marketplace._pip_install("some-pkg")
        assert sp.call_args.args[-1] == "some-pkg"
        assert len(sp.call_args.args) == 6  # python, -m, pip, install, --no-input, pkg
        assert all(isinstance(a, str) for a in sp.call_args.args)

    def test_is_importable(self):
        marketplace = PluginMarketplace(PluginRegistry(), PluginLoader())
        assert marketplace._is_importable("sys") is True
        assert marketplace._is_importable("this_module_does_not_exist_xyz") is False


# ---------------------------------------------------------------------------
# 4.3 — dependency resolution
# ---------------------------------------------------------------------------


class _DepPlugin(PluginBase):
    plugin_id = "dep-plugin"
    name = "Dep Plugin"
    version = "1.0.0"
    author = "tester"
    description = "plugin with deps"
    dependencies = ["requests==2.31.0", "numpy"]

    def config_schema(self) -> dict:
        return {}


class TestDependencyResolution:
    @pytest.mark.anyio
    async def test_register_populates_dependencies(self):
        registry = PluginRegistry()
        rec = await registry.register(_DepPlugin(), source="test")
        assert rec.dependencies == ["requests==2.31.0", "numpy"]

    def test_resolve_dependencies_version_mismatch(self):
        registry = PluginRegistry()
        registry._plugins["requests"] = _fake_record(
            "requests", "requests", "2.0.0", []
        )
        registry._plugins["dep-plugin"] = _fake_record(
            "dep-plugin", "Dep Plugin", "1.0.0", ["requests==9.9.9"]
        )
        conflicts = registry.resolve_dependencies("dep-plugin")
        assert any(c.get("status") == "version_mismatch" for c in conflicts)

    def test_resolve_dependencies_no_conflict(self):
        registry = PluginRegistry()
        registry._plugins["dep-plugin"] = _fake_record(
            "dep-plugin", "Dep Plugin", "1.0.0", []
        )
        assert registry.resolve_dependencies("dep-plugin") == []


def _fake_record(pid, name, version, deps):
    return type(
        "R",
        (),
        {
            "plugin_id": pid,
            "name": name,
            "version": version,
            "dependencies": deps,
        },
    )()


# ---------------------------------------------------------------------------
# 4.4 — AST import scan (pre-exec)
# ---------------------------------------------------------------------------


def _write_module(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    return path


class TestAstImportScan:
    def test_blocked_import_detected_preexec(self, tmp_path):
        path = _write_module(
            tmp_path,
            "evil_os",
            "import os\n"
            "class Evil(PluginBase):\n"
            "    plugin_id='evil.os'\n"
            "    def config_schema(self): return {}\n",
        )
        loader = PluginLoader(sandbox=PluginSandbox())
        with pytest.raises(PluginSecurityError):
            loader._load_module_from_path(path)

    def test_from_import_blocked(self, tmp_path):
        path = _write_module(tmp_path, "evil_sub", "from subprocess import Popen\n")
        sandbox = PluginSandbox()
        violations = sandbox.scan_source_for_blocked_imports(
            path.read_text(encoding="utf-8")
        )
        assert "subprocess" in violations

    def test_safe_import_ok(self, tmp_path):
        path = _write_module(tmp_path, "ok_json", "import json\nvalue = 1\n")
        loader = PluginLoader(sandbox=PluginSandbox())
        assert loader.load_from_directory(tmp_path) == {}


# ---------------------------------------------------------------------------
# 4.5 — ed25519 signing
# ---------------------------------------------------------------------------


class TestSigning:
    def _keypair(self, tmp_path: Path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        priv = Ed25519PrivateKey.generate()
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub_path = tmp_path / "evalops_pub.pem"
        priv_path = tmp_path / "evalops_priv.pem"
        pub_path.write_bytes(pub_pem)
        priv_path.write_bytes(priv_pem)
        return pub_path, priv_path

    def test_verify_signature_good(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        pub_path, priv_path = self._keypair(tmp_path)
        sandbox = PluginSandbox()
        src = tmp_path / "plugin_pkg.py"
        data = b"print('hello evalops plugin')\n"
        src.write_bytes(data)
        priv = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
        sig = priv.sign(data)
        (tmp_path / "plugin_pkg.py.sig").write_text(base64.b64encode(sig).decode())

        fake_dist = _FakeDist(tmp_path)
        with patch("importlib.metadata.distribution", return_value=fake_dist):
            # Should not raise.
            sandbox.verify_signature("plugin_pkg", public_key_path=pub_path)

    def test_verify_signature_tampered(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        pub_path, priv_path = self._keypair(tmp_path)
        sandbox = PluginSandbox()
        src = tmp_path / "plugin_pkg.py"
        data = b"print('hello evalops plugin')\n"
        src.write_bytes(data)
        priv = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
        sig = priv.sign(data)
        (tmp_path / "plugin_pkg.py.sig").write_text(base64.b64encode(sig).decode())

        # Tamper the source AFTER signing -> verification must fail.
        src.write_bytes(b"print('tampered')\n")
        fake_dist = _FakeDist(tmp_path)
        with patch("importlib.metadata.distribution", return_value=fake_dist):
            with pytest.raises(PluginSecurityError):
                sandbox.verify_signature("plugin_pkg", public_key_path=pub_path)

    def test_missing_key_advisory(self, tmp_path):
        sandbox = PluginSandbox()
        missing = tmp_path / "nope.pem"
        with pytest.raises(Exception):
            sandbox.verify_signature("x", public_key_path=missing)

    def test_config_env_gating(self, monkeypatch):
        monkeypatch.setenv("EVALOPS_REQUIRE_SIGNED", "true")
        assert PluginSecurityConfig().require_signed is True
        monkeypatch.setenv("EVALOPS_REQUIRE_SIGNED", "false")
        assert PluginSecurityConfig().require_signed is False


class _FakeDist:
    def __init__(self, base: Path) -> None:
        self._base = base
        self.files = [_File(base / "plugin_pkg.py")]

    def locate_file(self, f):
        return f._p


class _File:
    def __init__(self, p: Path) -> None:
        self._p = p
        self.suffix = p.suffix
        self.name = p.name


# ---------------------------------------------------------------------------
# 4.6 — resource limits (Windows no-op)
# ---------------------------------------------------------------------------


class TestResourceLimits:
    def test_resource_limited_is_safe_noop_on_windows(self):
        sandbox = PluginSandbox(max_memory_bytes=1024, max_cpu_seconds=1)
        with sandbox.resource_limited("plugin-x"):
            pass

    def test_resource_limited_caps_passed_through(self):
        sandbox = PluginSandbox(max_memory_bytes=4096)
        assert sandbox._max_mem == 4096
        assert sandbox._max_cpu is None


# ---------------------------------------------------------------------------
# 4.7 — hook gaps
# ---------------------------------------------------------------------------


class _HookPlugin2(PluginBase):
    plugin_id = "hook-plugin-2"
    name = "Hook Plugin 2"
    version = "1.0.0"

    def __init__(self) -> None:
        self.enabled_called = False
        self.uninstalled_called = False

    def config_schema(self) -> dict:
        return {}

    def on_enable(self) -> None:
        self.enabled_called = True

    def on_uninstall(self) -> None:
        self.uninstalled_called = True


class TestHookGaps:
    @pytest.mark.anyio
    async def test_uninstall_fires_from_instances_map(self):
        registry = PluginRegistry()
        loader = PluginLoader()
        marketplace = PluginMarketplace(registry, loader)
        plugin = _HookPlugin2()
        await registry.register(plugin, source="dir:hook-plugin-2")
        marketplace._instances["hook-plugin-2"] = plugin

        result = await marketplace.uninstall("hook-plugin-2")
        assert result.success is True
        assert plugin.uninstalled_called is True

    @pytest.mark.anyio
    async def test_install_fires_on_enable_via_set_enabled(self):
        registry = PluginRegistry()
        loader = PluginLoader()
        marketplace = PluginMarketplace(registry, loader)
        plugin = _HookPlugin2()

        await registry.register(plugin, source="dir:hook-plugin-2")
        marketplace._instances["hook-plugin-2"] = plugin
        await registry.set_enabled("hook-plugin-2", True, plugin=plugin)
        assert plugin.enabled_called is True


# ---------------------------------------------------------------------------
# 4.8 — pack unpacking
# ---------------------------------------------------------------------------


class TestPackUnpacking:
    @pytest.mark.anyio
    async def test_healthcare_pack_registers_children(self):
        loader = PluginLoader()
        packs_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "backend" / "plugins" / "packs"
        )
        plugins = loader.load_from_directory(packs_dir)
        assert "pack.healthcare" in plugins
        for child in [
            "healthcare.hipaa_compliance",
            "healthcare.phi_detection",
            "healthcare.clinical_validator",
            "healthcare.fhir_integration",
        ]:
            assert child in plugins, f"missing child {child}"
        assert len(plugins) == 5
