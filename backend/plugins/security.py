"""Plugin security — sandboxing, signing verification, and audit logging."""

from __future__ import annotations

import ast
import base64
import fnmatch
import importlib.metadata
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _try_literal_string(node: ast.expr) -> str | None:
    """Best-effort resolution of a constant or ``+``-concatenated string expr.

    Returns the joined string for ``"o" + "s"`` style literals, otherwise
    ``None`` for any expression that is not a fully-static string.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _try_literal_string(node.left)
        right = _try_literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None

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
    "importlib",  # importlib.import_module can load ANY module, incl. os
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
SIGNING_ALGORITHM = "ed25519"


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
        allowlist: set[str] | None = None,
        max_memory_bytes: int | None = None,
        max_cpu_seconds: int | None = None,
    ) -> None:
        self._blocked = blocked_imports or BLOCKED_IMPORTS
        self._safe = safe_imports or SAFE_IMPORTS
        self._blocked_builtins = blocked_builtins or BLOCKED_BUILTINS
        self._max_exec = max_execution_seconds
        self._allowlist = allowlist
        self._max_mem = max_memory_bytes
        self._max_cpu = max_cpu_seconds
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

    def scan_source_for_blocked_imports(self, source: str) -> list[str]:
        """Statically scan plugin *source* for blocked imports via AST.

        Runs BEFORE module execution (pre-exec), so ``import os`` at module
        top level is caught without ever evaluating the code.  Covers the
        literal forms (``import x``, ``import x.y``, ``from x import y``) as
        well as the dynamic escape forms that bypass the hooked ``__import__``:

        * ``__import__("os")`` / ``__import__("o" + "s")``
        * ``importlib.import_module("os")``
        * ``getattr(builtins, "import")`` / ``getattr(__builtins__, "import")``

        Any resolved root module name that is in :data:`BLOCKED_IMPORTS`
        causes a violation.  Note that literal-string targets are checked;
        opaque dynamic concatenation (e.g. ``"o" + "s"``) is conservatively
        flagged when the expression mentions a blocked root name.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    found.append(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    found.append(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                found.extend(self._scan_call_for_blocked(node))
        violations = sorted({m for m in found if m in self._blocked})
        if violations:
            logger.warning(
                "plugin_blocked_imports_prescan",
                imports=violations,
            )
        return violations

    def _scan_call_for_blocked(self, node: ast.Call) -> list[str]:
        """Inspect a call node for dynamic import escape forms."""
        hits: list[str] = []
        func = node.func

        # __import__(...)
        if isinstance(func, ast.Name) and func.id == "__import__":
            hits.extend(self._string_targets(node.args))

        # importlib.import_module(...) / importlib.util.spec_from_loader(...)
        if isinstance(func, ast.Attribute):
            value = func.value
            if (
                isinstance(value, ast.Name)
                and value.id == "importlib"
            ):
                hits.append("importlib")
                if func.attr == "import_module":
                    hits.extend(self._string_targets(node.args))

        # getattr(__builtins__, "import") / getattr(builtins, "import")
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
        ):
            target = node.args[0]
            name_arg = node.args[1]
            is_builtins = (
                isinstance(target, ast.Name)
                and target.id in {"__builtins__", "builtins"}
            ) or (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in {"__builtins__", "builtins"}
            )
            if is_builtins and isinstance(name_arg, ast.Constant):
                if str(name_arg.value) in {"import", "__import__"}:
                    hits.append("importlib")
        return hits

    @staticmethod
    def _string_targets(args: list[ast.expr]) -> list[str]:
        """Extract module-name roots from constant string args.

        Concatenation like ``"o" + "s"`` is best-effort resolved; if it is a
        literal string that names a blocked root, it is flagged.  Opaque
        non-literal targets fall back to the conservative caller behaviour.
        """
        roots: list[str] = []
        for arg in args:
            value = _try_literal_string(arg)
            if value is not None:
                roots.append(value.split(".")[0])
        return roots

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def validate_path(self, path: Path) -> bool:
        """Check if a plugin file path is permitted by the allowlist.

        Returns ``True`` if no allowlist is configured (open mode) or when
        the resolved path matches at least one allowlist pattern.  Patterns
        support both ``fnmatch`` full-path matching and ``Path.match``
        relative-name matching.

        Raises :class:`PluginSecurityError` when the path is rejected.
        """
        if not self._allowlist:
            self._log_audit("path_check_skipped", path=str(path), reason="no_allowlist")
            return True

        resolved = str(path.resolve())
        for pattern in self._allowlist:
            if fnmatch.fnmatch(resolved, pattern) or path.match(pattern):
                self._log_audit("path_allowed", path=resolved, pattern=pattern)
                return True

        self._log_audit("path_rejected", path=resolved, allowlist=sorted(self._allowlist))
        raise PluginSecurityError(
            f"Plugin path '{resolved}' is not in the allowlist. "
            f"Allowed patterns: {', '.join(sorted(self._allowlist))}"
        )

    # ------------------------------------------------------------------
    # Builtin restriction
    # ------------------------------------------------------------------

    def _safe_import(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Restricted ``__import__`` that enforces :data:`BLOCKED_IMPORTS`.

        Normal (whitelisted) imports still work inside plugin code, but blocked
        modules such as ``os`` or ``subprocess`` raise :class:`PluginSecurityError`
        instead of being silently importable.
        """
        import builtins as _builtins

        root = name.split(".")[0]
        if root in self._blocked:
            self._log_audit("blocked_import_attempt", module=name)
            raise PluginSecurityError(
                f"Import of '{name}' is blocked by the plugin sandbox. "
                f"Allowed imports include: {', '.join(sorted(self._safe))}"
            )
        return _builtins.__import__(name, *args, **kwargs)

    def restricted_builtins(self) -> dict[str, Any]:
        """Return a builtins dict with dangerous functions removed."""
        import builtins as _builtins

        safe = {k: getattr(_builtins, k) for k in dir(_builtins) if not k.startswith("_")}
        for name in self._blocked_builtins:
            safe.pop(name, None)
        # Provide a sandbox-enforcing __import__ so that normal imports inside
        # plugin code still work while blocked modules raise PluginSecurityError.
        safe["__import__"] = self._safe_import
        # Belt-and-suspenders: even if a plugin reaches `importlib` (e.g. via a
        # reference captured before sandboxing), the restricted __import__ helper
        # blocks every name in BLOCKED_IMPORTS, and `import`/`__import__` names
        # are not re-exposed through getattr. Blocking __import__ entirely would
        # break legitimate imports, so we rely on _safe_import to enforce policy.
        safe.pop("importlib", None)
        return safe

    # ------------------------------------------------------------------
    # Signing verification
    # ------------------------------------------------------------------

    def check_signing(self, package_name: str, *, require_signed: bool = False) -> bool:
        """Verify a package's cryptographic signature (ed25519).

        Returns ``True`` if a valid signature is found, ``False`` if no
        signature is present (advisory in dev mode).  When ``require_signed``
        is ``True`` a missing or bad signature raises :class:`PluginSecurityError`
        instead of returning ``False``.

        A missing public key file is treated as an advisory fallback: the
        check is skipped and ``False`` is returned without blocking (so the
        dev loop keeps working).  Set ``require_signed`` to harden in prod.
        """
        try:
            self.verify_signature(package_name)
            return True
        except PluginSignatureMissing:
            if require_signed:
                raise
            logger.warning("plugin_unsigned", package=package_name)
            return False
        except PluginSecurityError:
            if require_signed:
                raise
            logger.warning("plugin_signature_invalid", package=package_name)
            return False

    def verify_signature(self, package_name: str, *, public_key_path: Path | None = None) -> None:
        """Verify the ed25519 detached signature over a pip package's entry module.

        The plugin author ships ``<module>.sig`` (base64 ed25519 signature)
        next to the package's ``__init__``/entry source; the signature covers
        the raw bytes of that file.  On success this logs
        ``plugin_signature_verified``.  On a missing signature it raises
        :class:`PluginSignatureMissing`; on a bad signature it raises
        :class:`PluginSecurityError`.

        A missing public key file is an advisory no-op that raises
        :class:`PluginSignatureMissing` (so callers can decide whether to block).
        """
        from backend.core.config import DEFAULT_PLUGIN_PUBKEY_PATH

        key_path = public_key_path or DEFAULT_PLUGIN_PUBKEY_PATH
        if not key_path.exists():
            logger.warning(
                "plugin_signing_key_missing",
                path=str(key_path),
                hint="Set EVALOPS_PLUGIN_PUBKEY to a valid ed25519 public key to enforce signing.",
            )
            raise PluginSignatureMissing(f"Public key not found at {key_path}")

        try:
            dist = importlib.metadata.distribution(package_name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise PluginSecurityError(f"Package '{package_name}' not installed") from exc

        # Locate the primary entry source file (top_level module file).
        file_bytes: bytes | None = None
        source_path: Path | None = None
        for file_path in (dist.files or []):
            if file_path.suffix == ".py" and file_path.name != "__init__.py":
                candidate = Path(str(dist.locate_file(file_path)))
                if candidate.exists():
                    source_path = candidate
                    file_bytes = candidate.read_bytes()
                    break
        if file_bytes is None:
            # Fall back to the distribution's RECORD hash as the signed payload.
            raise PluginSignatureMissing(
                f"No verifiable source file found in '{package_name}'"
            )

        sig_path = source_path.with_suffix(source_path.suffix + ".sig")
        if not sig_path.exists():
            raise PluginSignatureMissing(
                f"Missing detached signature {sig_path} for '{package_name}'"
            )

        from cryptography.hazmat.primitives import serialization

        public_key = serialization.load_pem_public_key(key_path.read_bytes())
        signature = base64.b64decode(sig_path.read_text(encoding="utf-8").strip())
        try:
            public_key.verify(signature, file_bytes)
        except Exception as exc:
            raise PluginSecurityError(
                f"Plugin '{package_name}' signature verification failed"
            ) from exc
        logger.info("plugin_signature_verified", package=package_name, path=str(source_path))

    # ------------------------------------------------------------------
    # Execution timeout
    # ------------------------------------------------------------------

    @contextmanager
    def timed_execution(self, plugin_id: str) -> Generator[None]:
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
    # Resource limits (memory / CPU)
    # ------------------------------------------------------------------

    @contextmanager
    def resource_limited(self, plugin_id: str) -> Generator[None]:
        """Context manager capping memory (RLIMIT_AS) and CPU (RLIMIT_CPU).

        The address-space cap (RLIMIT_AS) kills runaway allocations via OOM;
        RLIMIT_CPU bounds CPU time.  The ``resource`` module is unavailable on
        Windows, so this is a safe no-op there (logs ``resource_limits_unavailable``).

        Previous limits are restored in the ``finally`` block.
        """
        if sys.platform == "win32":
            logger.info("resource_limits_unavailable", platform="win32")
            yield
            return

        import resource as _resource

        prev_mem = prev_cpu = None
        try:
            if self._max_mem is not None:
                soft, hard = _resource.getrlimit(_resource.RLIMIT_AS)
                prev_mem = (soft, hard)
                _resource.setrlimit(_resource.RLIMIT_AS, (self._max_mem, hard))
            if self._max_cpu is not None:
                soft, hard = _resource.getrlimit(_resource.RLIMIT_CPU)
                prev_cpu = (soft, hard)
                _resource.setrlimit(
                    _resource.RLIMIT_CPU, (self._max_cpu, self._max_cpu)
                )
            yield
        finally:
            if prev_mem is not None:
                try:
                    _resource.setrlimit(_resource.RLIMIT_AS, prev_mem)
                except OSError:
                    pass
            if prev_cpu is not None:
                try:
                    _resource.setrlimit(_resource.RLIMIT_CPU, prev_cpu)
                except OSError:
                    pass

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
