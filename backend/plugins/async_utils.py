"""Async helpers for the plugin system.

Centralizes the ``maybe_await`` coroutine so that sync and async plugin
lifecycle/score hooks can both be invoked uniformly.  CPU-bound plugin work
is pushed to a worker thread via :func:`anyio.to_thread.run_sync`.
"""

from __future__ import annotations

import inspect

import anyio


async def maybe_await(obj: object) -> object:
    """Await *obj* if it is awaitable, otherwise return it unchanged.

    This lets callers treat ``sync`` and ``async`` plugin hooks identically::

        result = await maybe_await(plugin.on_install())
        result = await maybe_await(await plugin.on_install_async())
    """
    if inspect.isawaitable(obj):
        return await obj
    return obj


async def run_sync(func, *args: object, **kwargs: object) -> object:
    """Run a (possibly blocking) sync callable on a worker thread."""
    return await anyio.to_thread.run_sync(func, *args, **kwargs)
