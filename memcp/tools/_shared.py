"""What every tool module shares: tool annotations and error responses."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from memcp.types import MemoryAPIError, PolicyError, canonical_error, log_backend_error

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
DESTRUCTIVE = {"destructiveHint": True}


def _backend_error(e: MemoryAPIError, tool: str) -> dict[str, Any]:
    """Log the store's response, then map the failure to a canonical error: a timeout
    becomes `timeout`; anything else is `backend_error`, retryable when the status is 5xx."""
    log_backend_error(e, tool)
    if e.timed_out:
        return canonical_error("timeout", e.client_message, retry=True)
    return canonical_error("backend_error", e.client_message, retry=e.status >= 500)


def translate_errors(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Return a policy refusal or a backend failure as the tool's error response."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except PolicyError as e:
            return canonical_error(e.code, e.message)
        except MemoryAPIError as e:
            return _backend_error(e, fn.__name__)

    return wrapper
