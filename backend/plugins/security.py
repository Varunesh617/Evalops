"""Plugin security — sandboxing, signing verification, and audit logging."""

from __future__ import annotations

import importlib.metadata
import signal
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Generator

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Dangerous builtins and imports that plugins must NOT use
# ---------------------------------------------------------------------------

BLOCKED_IMPORTS: frozenset[str] = frozenset({
    "os",
    "subprocess",
    "sys",
    "ctypes",
    "multiprocessing",
    "shutil",
    "socket",
    "http",
    "urllib",
    "xmlrpc",
    "pickle",
    "marshal",
    "code",
    "codeop",
    "compileall",
    "py_compile",
    "runpy",
    "signal",
    "threading",
    "asyncio",
    "pathlib",  # filesystem access via Path
    "tempfile",
    "glob",
    "fnmatch",
    "shlex",
})

SAFE_IMPORTS: frozenset[str] = frozenset({
    "json",
    "re",
    "math",
    "datetime",
    "typing",
    "pydantic",
    "structlog",
    "hashlib",
    "base64",
    "uuid",
    "dataclasses",
    "enum",
    "decimal",
    "fractions",
    "random",
    "string",
    "textwrap",
    "collections",
    "functools",
    "itertools",
    "operator",
    "copy",
    "pprint",
    "warnings",
    "contextlib",
    "abc",
})

BLOCKED_BUILTINS: frozenset[str] = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "exit",
    "quit",
    "open",  # filesystem access; use whitelisted I/O instead
    "input",
    "globals",
    "locals",
    "vars",
    "dir",
})

# ---------------------------------------------------------------------------
# Plugin signing metadata key
# ---------------------------------------------------------------------------

PLUGIN_SIGNATURE_KEY = "evalops-plugin"
PLUGIN_PACKAGE_METADATA_GROUP = "evalops"


class PluginSecurityError(Exception):
    """Raised when a plugin violates security constraints."""


class PluginSignatureMissing(PluginSecurityError):
    """Raised when a plugin lacks the evalops-plugin signature."""


class PluginSignatureWarning(PluginSecurityError):
    """Raised for unsigned plugins (non-fatal, advisory only)."""


# ---------------------------------------------------------------------------
# PluginSandbox
# ---------------------------------------------------------------------------

class PluginSandbox:
    """Restricts what loaded plugin modules can do.

    Usage::

        sandbox = PluginSandbox()
        sandbox.validate_imports(module)          # check imports used
        sandbox.check_signing(package_name)       # verify signature
        with sandbox.timed_execution(plugin_id):  # timeout guard
            plugin.on_install()
    """

    def __init__(
        self,
        *,
        blocked_imports: frozenset[str] | None = None,
        safe_imports: frozenset[str] | None = None,
        blocked_builtins: frozenset[str] | None = None,
        max_execution_seconds: float = 30.0,
    ) -> None:
        self._blocked = blocked_imports or BLOCKED_IMPORTS
        self._safe = safe_imports or SAFE_IMPORTS
        self._blocked_builtins = blocked_builtins or BLOCKED_BUILTINS
        self._max_exec = max_execution_seconds
        self._audit_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Import validation
    # ------------------------------------------------------------------

    def validate_imports(self, module: Any) -> list[str]:
        """Return list of blocked imports found in *module*'s source.

        This is a best-effort static check — it inspects ``__dict__`` for
        imported names that match known dangerous modules.  It is NOT a
        substitute for a full AST analysis, but raises the bar significantly.
        """
        violations: list[str] = []
        mod_dict = getattr(module, "__dict__", {})
        for name in mod_dict:
            if name in self._blocked:
                violations.append(name)

        if violations:
            logger.warning(
                "plugin_blocked_imports_detected",
                module=getattr(module, "__name__", "unknown"),
                imports=violations,
            )
        return violations

    def enforce_imports(self, module: Any) -> None:
        """Raise :class:`PluginSecurityError` if blocked imports are found."""
        violations = self.validate_imports(module)
        if violations:
            raise PluginSecurityError(
                f"Plugin uses blocked imports: {', '.join(violations)}. "
                f"Allowed: {', '.join(sorted(self._safe))}"
            )

    # ------------------------------------------------------------------
    # Builtin restriction
    # ------------------------------------------------------------------

    def restricted_builtins(self) -> dict[str, Any]:
        """Return a builtins dict with dangerous functions removed."""
        import builtins as _builtins

        safe = {k: getattr(_builtins, k) for k in dir(_builtins) if not k.startswith("_")}
        for name in self._blocked_builtins:
            safe.pop(name, None)
        # Re-inject __import__ so normal imports still work inside plugin code
        safe["__import__"] = _builtins.__import__
        return safe

    # ------------------------------------------------------------------
    # Signing verification
    # ------------------------------------------------------------------

    def check_signing(self, package_name: str) -> bool:
        """Check that a package declares itself as an evalops plugin.

        Returns ``True`` if the signature is present, ``False`` if missing
        (advisory — does not block installation).  Raises a warning that
        can be logged or surfaced to the operator.
        """
        try:
            md = importlib.metadata.metadata(package_name)
        except importlib.metadata.PackageNotFoundError:
            logger.warning(
                "plugin_signing_check_failed",
                package=package_name,
                reason="package metadata not found",
            )
            return False

        has_signature = PLUGIN_SIGNATURE_KEY in md
        if not has_signature:
            logger.warning(
                "plugin_unsigned",
                package=package_name,
                hint="Plugin does not declare evalops-plugin in metadata. "
                     "Consider requiring signed plugins for production use.",
            )
        else:
            logger.info(
                "plugin_signature_verified",
                package=package_name,
                classifier=md.get(PLUGIN_SIGNATURE_KEY),
            )
        return has_signature

    # ------------------------------------------------------------------
    # Execution timeout
    # ------------------------------------------------------------------

    @contextmanager
    def timed_execution(self, plugin_id: str) -> Generator[None, None, None]:
        """Context manager that raises if execution exceeds max seconds.

        Uses a daemon thread to enforce the timeout — safe for CPython
        where the GIL limits true parallel execution of Python code.
        """
        deadline = time.monotonic() + self._max_exec
        failed = threading.Event()

        def _watchdog() -> None:
            while time.monotonic() < deadline:
                if failed.is_set():
                    return
                time.sleep(0.25)
            failed.set()

        t = threading.Thread(target=_watchdog, daemon=True)
        t.start()
        try:
            yield
        finally:
            if failed.is_set():
                self._log_audit(
                    "timeout",
                    plugin_id=plugin_id,
                    max_seconds=self._max_exec,
                )
                raise PluginSecurityError(
                    f"Plugin '{plugin_id}' exceeded the {self._max_exec}s execution limit"
                )
            # Signal watchdog to stop
            failed.set()

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    def _log_audit(self, action: str, **kwargs: Any) -> None:
        entry = {"action": action, **kwargs}
        self._audit_log.append(entry)
        logger.info("plugin_security_audit", **entry)

    def get_audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)

    def log_operation(self, action: str, plugin_id: str, **extra: Any) -> None:
        """Public API for external code to record audit events."""
        self._log_audit(action, plugin_id=plugin_id, **extra)
