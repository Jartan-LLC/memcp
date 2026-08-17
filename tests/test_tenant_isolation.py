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

`mem0` is included when `MEM0_API_BASE`/`MEM0_API_KEY` are set (skipped otherwise —
see `tests/test_backend_mem0.py` for the same gate). It is the one backend where
isolation is not structural: `in_memory` and `sqlite` scope every query by `user_id`
in the query itself, but mem0's single-ID endpoints (GET/PUT/DELETE/history) and
`GET /entities` are global on the wire, so the adapter does fetch-then-verify and
post-filtering instead (`memcp/backend/mem0.py`). That is a correct design and also
the kind that fails silently when one call site forgets the check — JAR-452.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any, Literal

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

MEM0_API_BASE = os.environ.get("MEM0_API_BASE")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY")

BACKEND_PARAMS = [
    "in_memory",
    "sqlite",
    pytest.param(
        "mem0",
        marks=pytest.mark.skipif(
            not MEM0_API_BASE or not MEM0_API_KEY,
            reason="MEM0_API_BASE and MEM0_API_KEY not set",
        ),
    ),
]


def _config(tmp_path: Any, backend: Literal["in_memory", "sqlite", "mem0"]) -> Config:
    extra: dict[str, Any] = {}
    if backend == "sqlite":
        extra["memcp_sqlite_path"] = str(tmp_path / "isolation.sqlite3")
    elif backend == "mem0":
        extra["mem0_api_base"] = MEM0_API_BASE
        extra["mem0_api_key"] = MEM0_API_KEY
    return Config(
        MEMCP_BACKEND=backend,
        MEMCP_AUTH_TOKENS=f"{ALICE_TOKEN}:alice,{MALLORY_TOKEN}:mallory",
        MEMCP_HOST="127.0.0.1",
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


async def _tool_names(client: AsyncClient, token: str) -> set[str]:
    resp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"tools/list: HTTP {resp.status_code}"
    return {t["name"] for t in _parse(resp.text).get("result", {}).get("tools", [])}


def _text(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


@pytest.fixture(params=BACKEND_PARAMS)
def backend_name(request: pytest.FixtureRequest) -> Literal["in_memory", "sqlite", "mem0"]:
    return request.param


async def _cleanup_mem0(backend: Any, backend_name: str) -> None:
    """Purge alice's and mallory's memories from the live store after a test.

    `in_memory` and `sqlite` get a fresh backend per test (fresh dict, fresh
    tmp_path file); mem0 is one external store shared by every test function in
    this file, so leftovers from an earlier test would otherwise show up as
    unexplained extra memories in a later one.
    """
    if backend_name != "mem0":
        return
    for uid in ("alice", "mallory"):
        with contextlib.suppress(Exception):
            listing = await backend.list_memories(uid)
            for m in listing.memories:
                with contextlib.suppress(Exception):
                    await backend.delete(uid, m.id)


async def test_mallory_cannot_reach_alices_memory_through_any_tool(
    tmp_path: Any, backend_name: Literal["in_memory", "sqlite", "mem0"]
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

        # memory_entities is only registered by a backend that declares a graph, and
        # neither keyless backend does any more. Where it exists it is held to the
        # same bar; where it does not, its absence is the assertion.
        if "memory_entities" in await _tool_names(client, MALLORY_TOKEN):
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

    await _cleanup_mem0(backend, backend_name)
    await backend.close()


async def test_an_unknown_token_reaches_no_tenant_at_all(
    tmp_path: Any, backend_name: Literal["in_memory", "sqlite", "mem0"]
):
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

    await _cleanup_mem0(backend, backend_name)
    await backend.close()


async def test_two_tenants_write_the_same_content_without_colliding(
    tmp_path: Any, backend_name: Literal["in_memory", "sqlite", "mem0"]
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

    await _cleanup_mem0(backend, backend_name)
    await backend.close()


async def test_memory_status_carries_no_tenant_data(
    tmp_path: Any, backend_name: Literal["in_memory", "sqlite", "mem0"]
):
    """memory_status takes no user_id and calls the backend with none — it reports
    server config (backend type, version, capabilities), not memory content, so two
    tenants calling it get the identical answer. Covers the tool G7's unit test
    skips: it cannot leak because it carries no per-tenant state to leak (JAR-452)."""
    app, backend = create_app(_config(tmp_path, backend_name))

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        await _call(client, ALICE_TOKEN, "add_memory", {"content": SECRET, "infer": False})

        alice_status = await _call(client, ALICE_TOKEN, "memory_status", {})
        mallory_status = await _call(client, MALLORY_TOKEN, "memory_status", {})

        assert alice_status == mallory_status, "memory_status differs by tenant"
        assert SECRET not in _text(alice_status), "memory_status carried memory content"

    await _cleanup_mem0(backend, backend_name)
    await backend.close()


async def test_import_cannot_target_another_tenants_memory(
    tmp_path: Any, backend_name: Literal["in_memory", "sqlite", "mem0"]
):
    """import_memories only reads content/scope/metadata off each entry
    (memcp/migrate.py:import_payload) — a caller-supplied `id` is not a field it
    looks at, and on_conflict='overwrite' only ever dedup-matches against the
    caller's own tenant (`build_dedup_index` is scoped to `user_id`). So Mallory
    importing an entry that names Alice's memory_id, with content identical to
    Alice's, should create a new memory of Mallory's own rather than landing on
    Alice's (JAR-452)."""
    app, backend = create_app(_config(tmp_path, backend_name))

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        added = await _call(client, ALICE_TOKEN, "add_memory", {"content": SECRET, "infer": False})
        memory_id = added["results"][0]["id"] if "results" in added else added["id"]

        imported = await _call(
            client,
            MALLORY_TOKEN,
            "import_memories",
            {
                "memories": [{"id": memory_id, "content": SECRET}],
                "on_conflict": "overwrite",
            },
        )
        assert imported.get("imported") == 1, imported
        new_id = imported["results"][0]["id"]
        assert new_id != memory_id, "import landed on another tenant's memory id"
        assert imported["results"][0]["action"] == "created", (
            "import matched another tenant's dedup index instead of creating fresh"
        )

        still_there = await _call(client, ALICE_TOKEN, "get_memory", {"memory_id": memory_id})
        assert SECRET in _text(still_there), "cross-tenant import overwrote it"

        mallory_list = await _call(client, MALLORY_TOKEN, "list_memories", {})
        assert len(mallory_list["memories"]) == 1

    await _cleanup_mem0(backend, backend_name)
    await backend.close()
