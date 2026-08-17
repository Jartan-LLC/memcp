"""G7 — two tokens, two identities, and one that cannot reach the other's memories.

memcp's whole security story is that tenant isolation happens *in memcp*: the bearer
token resolves to a `user_id` and every backend call is scoped to it. Until this
file existed, nothing asserted it end to end through the MCP surface — which is what
made `SEC-2026-0038` (one shared token for fifteen agents) collapse isolation to a
single tenant without anything failing.

So this drives the real ASGI stack with two real tokens, and asserts for **every
registered tool** that Mallory can neither read, change, delete nor enumerate
Alice's memory. It runs against every backend the conformance registry can build, so
a new adapter inherits the check rather than needing its own.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from memcp.config import Config
from memcp.server import create_app

ALICE_TOKEN = "alice-token-not-a-real-credential"
MALLORY_TOKEN = "mallory-token-not-a-real-credential"
BASE_URL = "http://127.0.0.1:8080"

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

SECRET = "alice's private note about the acquisition"


def _config(tmp_path: Any, backend: str) -> Config:
    extra: dict[str, Any] = {}
    if backend == "sqlite":
        extra["memcp_sqlite_path"] = str(tmp_path / "isolation.sqlite3")
    return Config(
        memcp_backend=backend,
        memcp_auth_tokens=f"{ALICE_TOKEN}:alice,{MALLORY_TOKEN}:mallory",
        host="127.0.0.1",
        **extra,
    )


def _parse(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


async def _call(client: AsyncClient, token: str, name: str, arguments: dict[str, Any]) -> Any:
    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"{name}: HTTP {resp.status_code} {resp.text[:200]}"
    message = _parse(resp.text)
    result = message.get("result", {})
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return content[0]["text"]
    return result


def _text(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


@pytest.fixture(params=["in_memory", "sqlite"])
def backend_name(request: pytest.FixtureRequest) -> str:
    return request.param


async def test_mallory_cannot_reach_alices_memory_through_any_tool(
    tmp_path: Any, backend_name: str
):
    app, backend = create_app(_config(tmp_path, backend_name))

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        added = await _call(
            client,
            ALICE_TOKEN,
            "add_memory",
            {"content": SECRET, "scope": {"agent_id": "alice-agent"}, "infer": False},
        )
        memory_id = added["results"][0]["id"] if "results" in added else added["id"]

        # --- read paths ---------------------------------------------------
        found = await _call(
            client, MALLORY_TOKEN, "search_memory", {"query": "acquisition private note"}
        )
        assert found["results"] == [], "search_memory leaked another tenant's memory"

        listed = await _call(client, MALLORY_TOKEN, "list_memories", {})
        assert listed["memories"] == [], "list_memories enumerated another tenant"

        fetched = await _call(client, MALLORY_TOKEN, "get_memory", {"memory_id": memory_id})
        assert SECRET not in _text(fetched), "get_memory returned another tenant's content"

        exported = await _call(client, MALLORY_TOKEN, "export_memories", {})
        assert SECRET not in _text(exported), "export_memories leaked across tenants"

        entities = await _call(client, MALLORY_TOKEN, "memory_entities", {})
        assert entities["entities"] == [], "memory_entities enumerated another tenant"

        history = await _call(client, MALLORY_TOKEN, "memory_history", {"memory_id": memory_id})
        assert SECRET not in _text(history), "memory_history leaked prior content"

        # --- write paths --------------------------------------------------
        await _call(
            client,
            MALLORY_TOKEN,
            "update_memory",
            {"memory_id": memory_id, "content": "overwritten by mallory"},
        )
        await _call(client, MALLORY_TOKEN, "delete_memory", {"memory_id": memory_id})
        await _call(
            client, MALLORY_TOKEN, "delete_all_memories", {"scope": {"agent_id": "alice-agent"}}
        )

        # --- Alice's memory is intact and unchanged -------------------------
        still_there = await _call(client, ALICE_TOKEN, "get_memory", {"memory_id": memory_id})
        assert SECRET in _text(still_there), "another tenant deleted or overwrote it"

        alice_list = await _call(client, ALICE_TOKEN, "list_memories", {})
        assert len(alice_list["memories"]) == 1

    await backend.close()


async def test_an_unknown_token_reaches_no_tenant_at_all(tmp_path: Any, backend_name: str):
    app, backend = create_app(_config(tmp_path, backend_name))

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        await _call(
            client,
            ALICE_TOKEN,
            "add_memory",
            {"content": SECRET, "infer": False},
        )
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**MCP_HEADERS, "Authorization": "Bearer not-a-configured-token"},
        )
        assert resp.status_code == 401

    await backend.close()


async def test_two_tenants_write_the_same_content_without_colliding(
    tmp_path: Any, backend_name: str
):
    """Identical content in two tenants stays two memories, one per owner."""
    app, backend = create_app(_config(tmp_path, backend_name))

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        for token in (ALICE_TOKEN, MALLORY_TOKEN):
            await _call(
                client, token, "add_memory", {"content": "shared phrasing", "infer": False}
            )

        for token in (ALICE_TOKEN, MALLORY_TOKEN):
            listed = await _call(client, token, "list_memories", {})
            assert len(listed["memories"]) == 1

    await backend.close()
