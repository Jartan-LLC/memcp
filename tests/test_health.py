"""Tests for the /health endpoint."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from memcp.config import Config
from memcp.server import create_app


async def test_health_endpoint_unhealthy():
    mem0_config = Config(
        MEMCP_BACKEND="mem0",
        mem0_api_base="http://localhost:9999",
        mem0_api_key="fake",
        MEMCP_HOST="127.0.0.1",
    )
    app, backend = create_app(mem0_config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["backend"] == "mem0"
    assert isinstance(data["latency_ms"], (int, float))
    await backend.close()


async def test_health_endpoint_healthy(config: Config):
    """Test 200/healthy path with in_memory backend."""
    app, backend = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["backend"] == "in_memory"
    assert data["latency_ms"] == 0.0
    await backend.close()
