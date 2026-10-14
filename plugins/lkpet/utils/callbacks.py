"""Adapter for the optional sync-or-async hooks accepted by ``core`` modules.

Several core systems need to notify a higher layer (biography, command
handlers) without importing it, so they take a callback and let this helper
normalise the result.
"""

import collections.abc
from inspect import isawaitable
from typing import Any

Callback = collections.abc.Callable[..., Any]
"""A hook that may be either a plain function or a coroutine function."""


async def run_callback(callback: Callback | None, *args: Any) -> Any:
    """Invoke ``callback``, awaiting it only when it returns an awaitable."""
    if callback is None:
        return None
    result = callback(*args)
    if isawaitable(result):
        return await result
    return result
