"""Application factory — assembles FastMCP server from config + backend."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from memcp.auth import BearerGate, StaticResolver
from memcp.backend import MemoryBackend
from memcp.backend.mem0 import Mem0Backend
from memcp.config import Config
from memcp.tools import register_tools

INSTRUCTIONS = """\
Long-term memory shared across your tools and sessions.

When to WRITE (add_memory): whenever a durable fact, preference, decision, or \
piece of context worth recalling later comes up — or when asked to remember \
something. Don't store transient one-off questions or throwaway debugging context.

When to SEARCH (search_memory): before answering anything that depends on what's \
already known about the user, their projects, preferences, or prior decisions. \
Search first; don't assume the memory is empty.

Filters are flat only: narrow with scope keys. \
Do not attempt nested boolean (AND/OR/NOT) filter expressions.

Destructive operations (delete_memory, delete_all_memories) should be confirmed \
with the user first; delete_all_memories requires a scope.
"""


def create_app(config: Config) -> tuple[Any, MemoryBackend]:
    """Build and return (asgi_app, backend)."""
    mcp = FastMCP(
        "memcp",
        instructions=INSTRUCTIONS,
        host=config.host,
        port=config.port,
        stateless_http=True,
    )

    backend = Mem0Backend(config.mem0_api_base, config.mem0_api_key)
    register_tools(mcp, backend, config)

    resolver = None
    if config.memcp_auth_tokens:
        resolver = StaticResolver.from_env(config.memcp_auth_tokens)

    mcp_app = BearerGate(mcp.streamable_http_app(), resolver)

    async def health(request: Request) -> JSONResponse:
        status = await backend.health()
        code = 200 if status.status == "healthy" else 503
        return JSONResponse(
            {"status": status.status, "backend": status.backend, "latency_ms": status.latency_ms},
            status_code=code,
        )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        yield
        await backend.close()

    app = Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )

    return app, backend
