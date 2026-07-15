"""Dynamic plugin loader — entry points, directories, and pip packages."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import sys
import types
from pathlib import Path
from typing import Any

import structlog

from backend.plugins.sdk import PluginBase
from backend.plugins.security import PluginSandbox, PluginSecurityError, PluginSignatureMissing

logger = structlog.get_logger(__name__)

ENTRY_POINT_GROUP = "evalops.plugins"
MIN_PLUGIN_VERSION = "0.1.0"
MAX_PLUGIN_VERSION = "2.0.0"


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded or validated."""


class VersionConflict(PluginLoadError):
    """Raised when a plugin version is incompatible."""


class PluginLoader:
    """Discovers and instantiates EvalOps plugins from multiple sources."""

    def __init__(
        self,
        *,
        extra_dirs: list[str | Path] | None = None,
        sandbox: PluginSandbox | None = None,
    ) -> None:
        self._extra_dirs = [Path(d) for d in (extra_dirs or [])]
        self._hot_reload_watches: dict[str, float] = {}
        self._loaded_modules: dict[str, types.ModuleType] = {}
        self._sandbox = sandbox

    # ------------------------------------------------------------------
    # Entry-point loading (setuptools / pyproject.toml)
    # ------------------------------------------------------------------

    def load_from_entry_points(self) -> dict[str, PluginBase]:
        """Load all plugins registered under the ``evalops.plugins`` entry-point group."""
        plugins: dict[str, PluginBase] = {}
        try:
            eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
        except TypeError:
            eps = importlib.metadata.entry_points().get(ENTRY_POINT_GROUP, [])

        for ep in eps:
            try:
                plugin = self._load_entry_point(ep)
                plugins[plugin.plugin_id] = plugin
            except PluginLoadError as exc:
                logger.warning("entry_point_load_failed", entry_point=ep.name, error=str(exc))
        return plugins

    def _load_entry_point(self, ep: importlib.metadata.EntryPoint) -> PluginBase:
        cls = None
        try:
            if self._sandbox is not None:
                with self._sandbox.timed_execution(ep.name):
                    cls = ep.load()
            else:
                cls = ep.load()
        except PluginSecurityError:
            raise
        except Exception as exc:
            raise PluginLoadError(f"Failed to load entry point '{ep.name}': {exc}") from exc

        plugin = self._instantiate_plugin(cls, source=f"entry_point:{ep.name}")

        if self._sandbox is not None:
            cls_module = inspect.getmodule(cls)
            if cls_module is not None:
                # Pre-exec AST scan (4.4): catch blocked + dynamic imports
                # (e.g. importlib.import_module, __import__("os")) before run.
                src_file = inspect.getsourcefile(cls)
                if src_file is not None:
                    src = Path(src_file).read_text(encoding="utf-8")
                    violations = self._sandbox.scan_source_for_blocked_imports(src)
                    if violations:
                        raise PluginSecurityError(
                            f"Entry point '{ep.name}' uses blocked imports: "
                            f"{', '.join(violations)}"
                        )
                # Enforce signing when EVALOPS_REQUIRE_SIGNED is enabled.
                self._enforce_signing(ep.name, Path(src_file) if src_file else None)
                cls_module.__dict__["__builtins__"] = self._sandbox.restricted_builtins()
                self._sandbox.enforce_imports(cls_module)

        return plugin

    # ------------------------------------------------------------------
    # Directory loading
    # ------------------------------------------------------------------

    def load_from_directory(self, directory: str | Path) -> dict[str, PluginBase]:
        """Scan a directory for Python modules containing PluginBase subclasses."""
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise PluginLoadError(f"Plugin directory does not exist: {dir_path}")

        plugins: dict[str, PluginBase] = {}
        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                discovered = self._load_module_from_path(py_file)
                plugins.update(discovered)
            except PluginLoadError as exc:
                logger.warning("directory_load_failed", file=str(py_file), error=str(exc))
        return plugins

    def load_from_extra_dirs(self) -> dict[str, PluginBase]:
        """Load plugins from all configured extra directories."""
        plugins: dict[str, PluginBase] = {}
        for extra_dir in self._extra_dirs:
            if extra_dir.is_dir():
                plugins.update(self.load_from_directory(extra_dir))
        return plugins

    def _load_module_from_path(self, path: Path) -> dict[str, PluginBase]:
        if self._sandbox is not None:
            self._sandbox.validate_path(path)

        # Pre-exec AST scan (4.4): catch blocked imports before any code runs.
        if self._sandbox is not None:
            src = path.read_text(encoding="utf-8")
            violations = self._sandbox.scan_source_for_blocked_imports(src)
            if violations:
                raise PluginSecurityError(
                    f"Module '{path}' uses blocked imports: {', '.join(violations)}"
                )

        module_name = f"evalops_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Cannot create module spec for {path}")

        if self._sandbox is not None:
            self._enforce_signing(path.stem, path)

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            if self._sandbox is not None:
                # Restrict what the plugin module can do at runtime by
                # swapping in the sandboxed builtins before execution.
                module.__dict__["__builtins__"] = self._sandbox.restricted_builtins()
                with (
                    self._sandbox.resource_limited(module_name),
                    self._sandbox.timed_execution(module_name),
                ):
                    spec.loader.exec_module(module)  # type: ignore[union-attr]
            else:
                spec.loader.exec_module(module)  # type: ignore[union-attr]
        except PluginSecurityError:
            raise
        except Exception as exc:
            raise PluginLoadError(f"Module execution failed for {path}: {exc}") from exc

        if self._sandbox is not None:
            self._sandbox.enforce_imports(module)

        self._loaded_modules[module_name] = module
        return self._discover_plugins_in_module(module, source=str(path))

    def _enforce_signing(self, package_name: str, source_path: Path | None = None) -> None:
        """Verify plugin signature when EVALOPS_REQUIRE_SIGNED is set.

        Uses :class:`PluginSandbox` signing checks.  A missing key is advisory
        (see ``PluginSecurityConfig``); a *bad* signature always raises.
        """
        from backend.core.config import PluginSecurityConfig

        cfg = PluginSecurityConfig()
        if not cfg.require_signed:
            return
        sandbox = self._sandbox
        if sandbox is None:
            return
        try:
            sandbox.check_signing(package_name, require_signed=True)
        except PluginSignatureMissing:
            raise PluginSecurityError(
                f"Plugin '{package_name}' is not signed; "
                f"EVALOPS_REQUIRE_SIGNED is enabled."
            )
        except PluginSecurityError as exc:
            raise PluginSecurityError(
                f"Plugin '{package_name}' signature verification failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Pip package loading
    # ------------------------------------------------------------------

    def load_from_pip(self, package_name: str) -> dict[str, PluginBase]:
        """Import a pip-installed package and extract EvalOps plugins from it."""
        if self._sandbox is not None:
            self._enforce_signing(package_name)
        try:
            module = importlib.import_module(package_name)
        except ImportError as exc:
            raise PluginLoadError(
                f"Cannot import package '{package_name}'. Is it installed?"
            ) from exc

        if self._sandbox is not None:
            self._sandbox.enforce_imports(module)

        self._loaded_modules[package_name] = module
        return self._discover_plugins_in_module(module, source=f"pip:{package_name}")

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def check_hot_reload(self) -> dict[str, PluginBase]:
        """Re-scan watched modules and reload any that changed on disk."""
        reloaded: dict[str, PluginBase] = {}
        for module_name, module in list(self._loaded_modules.items()):
            file_path = getattr(module, "__file__", None)
            if file_path is None:
                continue
            path = Path(file_path)
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            prev = self._hot_reload_watches.get(module_name)
            if prev is not None and mtime > prev:
                logger.info("hot_reload_triggering", module=module_name)
                try:
                    importlib.reload(module)
                    reloaded.update(
                        self._discover_plugins_in_module(module, source=f"reload:{module_name}")
                    )
                except Exception as exc:
                    logger.error("hot_reload_failed", module=module_name, error=str(exc))
            self._hot_reload_watches[module_name] = mtime
        return reloaded

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_plugin(obj: Any) -> PluginBase:
        """Ensure *obj* is a concrete PluginBase subclass and return it."""
        if inspect.isclass(obj) and issubclass(obj, PluginBase) and obj is not PluginBase:
            raise PluginLoadError(
                f"Got class {obj.__name__} — pass a class, not an instance"
            )
        if not isinstance(obj, PluginBase):
            raise PluginLoadError(
                f"Object {obj!r} is not an instance of PluginBase"
            )
        return obj

    @staticmethod
    def validate_version(version: str) -> None:
        """Check that a plugin version string is within the supported range."""
        from packaging.version import Version

        try:
            ver = Version(version)
        except Exception as exc:
            raise PluginLoadError(f"Invalid version string '{version}': {exc}") from exc

        if ver < Version(MIN_PLUGIN_VERSION):
            raise VersionConflict(
                f"Plugin version {version} is below minimum {MIN_PLUGIN_VERSION}"
            )
        if ver >= Version(MAX_PLUGIN_VERSION):
            raise VersionConflict(
                f"Plugin version {version} exceeds maximum {MAX_PLUGIN_VERSION}"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _instantiate_plugin(self, cls: Any, *, source: str = "unknown") -> PluginBase:
        if inspect.isclass(cls):
            if not issubclass(cls, PluginBase):
                raise PluginLoadError(
                    f"Class {cls.__name__} does not extend PluginBase (source={source})"
                )
            instance = cls()
        elif isinstance(cls, PluginBase):
            instance = cls
        else:
            raise PluginLoadError(
                f"Cannot instantiate plugin from {cls!r} (source={source})"
            )
        self.validate_version(instance.version)
        logger.info(
            "plugin_loaded",
            plugin_id=instance.plugin_id,
            version=instance.version,
            source=source,
        )
        return instance

    def _discover_plugins_in_module(
        self, module: types.ModuleType, *, source: str = "unknown"
    ) -> dict[str, PluginBase]:
        if self._sandbox is not None:
            self._sandbox.enforce_imports(module)

        plugins: dict[str, PluginBase] = {}
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                inspect.isclass(obj)
                and issubclass(obj, PluginBase)
                and obj is not PluginBase
                and not inspect.isabstract(obj)
            ):
                try:
                    instance = self._instantiate_plugin(obj, source=source)
                    plugins[instance.plugin_id] = instance
                except PluginLoadError as exc:
                    logger.warning(
                        "plugin_instantiate_failed",
                        class_name=obj.__name__,
                        error=str(exc),
                    )

        # Pack unpacking (4.8): detect pack classes and register their children.
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                inspect.isclass(obj)
                and issubclass(obj, PluginBase)
                and obj is not PluginBase
                and not inspect.isabstract(obj)
                and getattr(obj, "plugin_id", "").startswith("pack.")
                and hasattr(obj, "get_plugins")
            ):
                try:
                    pack_instance = obj()
                except Exception as exc:
                    logger.warning(
                        "plugin_pack_instantiate_failed",
                        class_name=obj.__name__,
                        error=str(exc),
                    )
                    continue
                for child in pack_instance.get_plugins():
                    if (
                        isinstance(child, PluginBase)
                        and child.plugin_id != pack_instance.plugin_id
                        and not inspect.isabstract(child)
                        and child.plugin_id not in plugins
                    ):
                        try:
                            self.validate_version(child.version)
                            plugins[child.plugin_id] = child
                            logger.info(
                                "plugin_pack_child_registered",
                                parent=pack_instance.plugin_id,
                                child=child.plugin_id,
                            )
                        except PluginLoadError as exc:
                            logger.warning(
                                "plugin_pack_child_failed",
                                parent=pack_instance.plugin_id,
                                child=getattr(child, "plugin_id", "?"),
                                error=str(exc),
                            )
        return plugins
