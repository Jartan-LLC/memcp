"""MCP tool registration: `register_tools` wires each tool module onto a server.

Each module's `register` defines its handlers as closures over the backend (and
config), so there are no module-level globals.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from memcp.backends import MemoryBackend
from memcp.config import Config
from memcp.tools import bulk, insight, memories, transfer

logger = logging.getLogger(__name__)


def _log_tool_call(fn: Any) -> Any:
    """Decorator that logs tool name, duration, and status."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        try:
            result = await fn(*args, **kwargs)
            duration_ms = (time.monotonic() - start) * 1000
            status = "error" if isinstance(result, dict) and "error" in result else "ok"
            logger.info(
                "tool=%s status=%s duration_ms=%.1f",
                fn.__name__,
                status,
                duration_ms,
            )
            return result
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "tool=%s status=exception duration_ms=%.1f",
                fn.__name__,
                duration_ms,
                exc_info=True,
            )
            raise

    return wrapper


def register_tools(mcp: Any, backend: MemoryBackend, config: Config) -> None:
    """Register all MCP tools on the given server instance."""

    # Wrap mcp.tool to auto-apply request logging
    _original_tool = mcp.tool

    def _logged_tool(**kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            return _original_tool(**kwargs)(_log_tool_call(fn))

        return decorator

    mcp.tool = _logged_tool  # type: ignore[assignment]

    memories.register(mcp, backend)
    bulk.register(mcp, backend)
    transfer.register(mcp, backend)
    insight.register(mcp, backend, config)
