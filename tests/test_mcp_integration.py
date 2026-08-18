"""Integration test — real MCP request through the full ASGI stack.

Exercises: MCPServer session manager → BearerGate → tool dispatch → backend.
Catches lifespan/initialization issues that unit tests miss.
"""

from __future__ import annotations

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from memcp.config import Config

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


async def test_mcp_endpoint_responds(config: Config):
    """POST to /mcp with a valid MCP initialize request succeeds."""
    from memcp.server import create_app

    app, _backend = create_app(config)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            },
            headers=MCP_HEADERS,
        )

    assert resp.status_code == 200, f"MCP init failed: {resp.status_code} {resp.text}"
    assert "result" in resp.text
    assert "error" not in resp.text


async def test_mcp_endpoint_with_auth():
    """MCP endpoint rejects unauthenticated, accepts authenticated."""
    from memcp.server import create_app

    auth_config = Config(
        memcp_backend="in_memory",
        memcp_auth_tokens="testtoken:testuser",
    )
    app, _backend = create_app(auth_config)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        # No auth — 401
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            },
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 401

        # With auth — 200
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            },
            headers={**MCP_HEADERS, "Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert "result" in resp.text
