"""Mem0Backend mock tests — covers all mem0-specific logic without a live server.

Uses respx to mock httpx requests. Tests the adapter's quirk handling:
fetch-then-verify ownership, GET-after-PUT, entities from the tenant's own memories,
error mapping, network error wrapping.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from memcp.backend.mem0 import LIST_CEILING, Mem0Backend, _entity_rows
from memcp.types import Memory, MemoryAPIError

BASE = "https://mem0.test"
KEY = "test-key"
USER = "alice"
OTHER = "bob"


@pytest.fixture
async def backend():
    b = Mem0Backend(BASE, KEY)
    yield b
    await b.close()


MEMORY_RESPONSE = {
    "id": "mem-1",
    "memory": "test content",
    "user_id": "alice",
    "metadata": None,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": None,
}


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@respx.mock
async def test_add_returns_results(backend):
    respx.post(f"{BASE}/memories").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "mem-1", "event": "ADD", "memory": "fact"}]}
        )
    )
    results = await backend.add(USER, "fact", infer=False)
    assert len(results) == 1
    assert results[0].id == "mem-1"


@respx.mock
async def test_add_empty_extraction(backend):
    respx.post(f"{BASE}/memories").mock(return_value=httpx.Response(200, json={"results": []}))
    results = await backend.add(USER, "nothing here", infer=True)
    assert results == []


# ---------------------------------------------------------------------------
# get — ownership verification
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_returns_memory(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    result = await backend.get(USER, "mem-1")
    assert result is not None
    assert result.content == "test content"


@respx.mock
async def test_get_wrong_user_returns_none(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    result = await backend.get(OTHER, "mem-1")
    assert result is None


@respx.mock
async def test_get_null_response(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(return_value=httpx.Response(200, content=b""))
    result = await backend.get(USER, "mem-1")
    assert result is None


# ---------------------------------------------------------------------------
# delete — fetch-then-verify
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_checks_ownership(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    respx.delete(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json={"message": "deleted"})
    )
    result = await backend.delete(USER, "mem-1")
    assert result is True


@respx.mock
async def test_delete_wrong_user_raises(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    delete_route = respx.delete(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json={"message": "deleted"})
    )
    with pytest.raises(MemoryAPIError, match="Not found"):
        await backend.delete(OTHER, "mem-1")
    assert delete_route.call_count == 0, "DELETE should never fire for wrong user"


@respx.mock
async def test_delete_nonexistent_raises(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(MemoryAPIError, match="Not found"):
        await backend.delete(USER, "mem-1")


# ---------------------------------------------------------------------------
# update — GET after PUT
# ---------------------------------------------------------------------------


@respx.mock
async def test_update_fetches_after_put(backend):
    respx.put(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json={"message": "updated"})
    )
    updated_response = {
        **MEMORY_RESPONSE,
        "memory": "new content",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=updated_response)
    )
    result = await backend.update(USER, "mem-1", "new content")
    assert result.content == "new content"


@respx.mock
async def test_update_wrong_user_raises(backend):
    put_route = respx.put(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json={"message": "updated"})
    )
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    with pytest.raises(MemoryAPIError, match="not found"):
        await backend.update(OTHER, "mem-1", "hijack")
    assert put_route.call_count == 0, "PUT should never fire for wrong user"


# ---------------------------------------------------------------------------
# entities — built from the tenant's own memories
# ---------------------------------------------------------------------------

# One store shared by two tenants. Mallory has written under agent_id "alice",
# and bob under "claude-code"; mem0's GET /entities would count both against
# whichever tenant shares that name.
SHARED_STORE = [
    {
        "id": "m1",
        "memory": "a",
        "user_id": "alice",
        "agent_id": "claude-code",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": None,
    },
    {
        "id": "m2",
        "memory": "b",
        "user_id": "alice",
        "agent_id": "claude-code",
        "run_id": "r1",
        "created_at": "2026-01-02T00:00:00Z",
        "updated_at": "2026-01-05T00:00:00Z",
    },
    {
        "id": "m3",
        "memory": "c",
        "user_id": "mallory",
        "agent_id": "alice",
        "created_at": "2026-01-03T00:00:00Z",
        "updated_at": None,
    },
    {
        "id": "m4",
        "memory": "d",
        "user_id": "bob",
        "agent_id": "claude-code",
        "created_at": "2026-01-04T00:00:00Z",
        "updated_at": None,
    },
]


# What GET /entities reports for SHARED_STORE: every tenant's values, bucketed.
SHARED_STORE_ENTITIES = [
    {"id": "alice", "type": "agent", "total_memories": 1},
    {"id": "claude-code", "type": "agent", "total_memories": 3},
    {"id": "r1", "type": "run", "total_memories": 1},
    {"id": "alice", "type": "user", "total_memories": 2},
    {"id": "bob", "type": "user", "total_memories": 1},
    {"id": "mallory", "type": "user", "total_memories": 1},
]


def _list_shared_store(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    rows = [
        row
        for row in SHARED_STORE
        if all(row.get(k) == params[k] for k in ("user_id", "agent_id", "run_id") if k in params)
    ]
    return httpx.Response(200, json={"results": rows})


@pytest.fixture
def shared_store():
    with respx.mock:
        global_entities = respx.get(f"{BASE}/entities").mock(
            return_value=httpx.Response(200, json=SHARED_STORE_ENTITIES)
        )
        respx.get(f"{BASE}/memories").mock(side_effect=_list_shared_store)
        yield global_entities


async def test_entities_count_only_the_callers_memories(backend, shared_store):
    result = await backend.entities(USER)
    assert result.entities == [
        {
            "id": "alice",
            "type": "user",
            "total_memories": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-05T00:00:00Z",
        },
        {
            "id": "claude-code",
            "type": "agent",
            "total_memories": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-05T00:00:00Z",
        },
        {
            "id": "r1",
            "type": "run",
            "total_memories": 1,
            "created_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-05T00:00:00Z",
        },
    ]
    assert shared_store.call_count == 0, "GET /entities mixes every tenant's values"


async def test_entities_never_show_another_tenants_memories(backend, shared_store):
    # A tenant whose name another tenant used as an agent_id sees none of it.
    result = await backend.entities("claude-code")
    assert result.entities == []


async def test_entities_limit_keeps_the_user_row(backend, shared_store):
    result = await backend.entities(USER, limit=1)
    assert [(e["type"], e["id"]) for e in result.entities] == [("user", "alice")]


def test_entity_rows_put_the_user_first_then_sort_by_type_and_id():
    memories = [
        Memory(id="m1", content="a", scope={"run_id": "r2", "agent_id": "b"}),
        Memory(id="m2", content="b", scope={"run_id": "r1", "agent_id": "a"}),
    ]
    assert [(e["type"], e["id"]) for e in _entity_rows(USER, memories)] == [
        ("user", "alice"),
        ("agent", "a"),
        ("agent", "b"),
        ("run", "r1"),
        ("run", "r2"),
    ]


@respx.mock
async def test_scope_user_id_never_replaces_the_tenant(backend):
    listed = respx.get(f"{BASE}/memories").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    searched = respx.post(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    added = respx.post(f"{BASE}/memories").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    scope = {"user_id": OTHER, "agent_id": "a"}

    await backend.list_memories(USER, scope=scope)
    await backend.search(USER, "q", scope=scope)
    await backend.add(USER, "fact", scope=scope, infer=False)

    assert listed.calls.last.request.url.params["user_id"] == USER
    assert json.loads(searched.calls.last.request.content)["filters"]["user_id"] == USER
    assert json.loads(added.calls.last.request.content)["user_id"] == USER


@respx.mock
@pytest.mark.parametrize("scope", [{"user_id": OTHER}, {"foo": "x"}])
async def test_delete_all_refuses_a_key_mem0_would_ignore(backend, scope):
    route = respx.delete(f"{BASE}/memories").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(MemoryAPIError) as exc:
        await backend.delete_all(USER, scope)
    assert exc.value.status == 400
    assert route.call_count == 0


def test_entity_rows_skip_missing_and_invalid_timestamps():
    memories = [
        Memory(id="m1", content="a", created_at="", updated_at=None),
        Memory(id="m2", content="b", created_at="not a date", updated_at=None),
        Memory(id="m3", content="c", created_at="2026-01-02T00:00:00Z", updated_at=None),
    ]
    [row] = _entity_rows(USER, memories)
    assert row["total_memories"] == 3
    assert (row["created_at"], row["updated_at"]) == (
        "2026-01-02T00:00:00Z",
        "2026-01-02T00:00:00Z",
    )


@respx.mock
@pytest.mark.parametrize("value", ["*", "", None, [], [""]])
async def test_delete_all_refuses_a_value_the_adapter_would_drop(backend, value):
    route = respx.delete(f"{BASE}/memories").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(MemoryAPIError) as exc:
        await backend.delete_all(USER, {"run_id": value})
    assert exc.value.status == 400
    assert route.call_count == 0


@respx.mock
async def test_delete_all_with_no_scope_deletes_the_tenant(backend):
    route = respx.delete(f"{BASE}/memories").mock(return_value=httpx.Response(200, json={}))
    await backend.delete_all(USER, {})
    assert dict(route.calls.last.request.url.params) == {"user_id": USER}


async def test_entities_respect_scope(backend, shared_store):
    result = await backend.entities(USER, scope={"run_id": "r1"})
    assert [(e["type"], e["id"], e["total_memories"]) for e in result.entities] == [
        ("user", "alice", 1),
        ("agent", "claude-code", 1),
        ("run", "r1", 1),
    ]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_parses_results(backend):
    respx.post(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "mem-1",
                        "memory": "Python fact",
                        "score": 0.95,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": None,
                    }
                ]
            },
        )
    )
    results = await backend.search(USER, "Python")
    assert len(results) == 1
    assert results[0].score == 0.95
    assert results[0].content == "Python fact"


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


@respx.mock
async def test_http_error_raises_memory_api_error(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(MemoryAPIError) as exc_info:
        await backend.get(USER, "mem-1")
    assert exc_info.value.status == 500


@respx.mock
async def test_network_error_raises_503(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(MemoryAPIError) as exc_info:
        await backend.get(USER, "mem-1")
    assert exc_info.value.status == 503
    assert "Network error" in str(exc_info.value)


@respx.mock
async def test_timeout_raises_408(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(MemoryAPIError) as exc_info:
        await backend.get(USER, "mem-1")
    assert exc_info.value.status == 408
    assert "Timeout" in str(exc_info.value)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@respx.mock
async def test_health_healthy(backend):
    respx.get(f"{BASE}/memories").mock(return_value=httpx.Response(200, json=[]))
    status = await backend.health()
    assert status.status == "healthy"


@respx.mock
async def test_health_unhealthy(backend):
    respx.get(f"{BASE}/memories").mock(side_effect=httpx.ConnectError("down"))
    status = await backend.health()
    assert status.status == "unhealthy"


# ---------------------------------------------------------------------------
# list_memories — pagination shim
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_memories_paginates(backend):
    mems = [
        {
            "id": f"m-{i}",
            "memory": f"mem {i}",
            "user_id": USER,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": None,
        }
        for i in range(5)
    ]
    respx.get(f"{BASE}/memories").mock(return_value=httpx.Response(200, json=mems))
    page1 = await backend.list_memories(USER, limit=2)
    assert len(page1.memories) == 2
    assert page1.next_cursor is not None


@respx.mock
async def test_list_memories_requests_the_server_ceiling(backend):
    """Canary: mem0's GET /memories defaults top_k to 20, so it must be explicit.

    Without it, list, export and the import dedup index all see the first 20
    memories and nothing tells the caller the rest exist.
    """
    route = respx.get(f"{BASE}/memories").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await backend.list_memories(USER, limit=100)
    assert route.calls.last.request.url.params["top_k"] == str(LIST_CEILING)
    assert LIST_CEILING == 1000, (
        "mem0 rejects top_k above ALL_MEMORIES_LIMIT (1000) in its server; raising "
        "this constant needs an upstream change first"
    )


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@respx.mock
async def test_history_parses_entries(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    respx.get(f"{BASE}/memories/mem-1/history").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "event": "ADD",
                    "created_at": "2026-01-01T00:00:00Z",
                    "old_memory": None,
                    "new_memory": "original",
                },
                {
                    "event": "UPDATE",
                    "created_at": "2026-01-02T00:00:00Z",
                    "old_memory": "original",
                    "new_memory": "updated",
                },
            ],
        )
    )
    entries = await backend.history(USER, "mem-1")
    assert len(entries) == 2
    assert entries[0].action == "add"
    assert entries[1].content_before == "original"


@respx.mock
async def test_history_wrong_user_raises(backend):
    respx.get(f"{BASE}/memories/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY_RESPONSE)
    )
    history_route = respx.get(f"{BASE}/memories/mem-1/history").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(MemoryAPIError, match="Not found"):
        await backend.history(OTHER, "mem-1")
    assert history_route.call_count == 0, "History endpoint should not be called for wrong user"
