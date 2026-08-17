"""Mem0Backend mock tests — covers all mem0-specific logic without a live server.

Uses respx to mock httpx requests. Tests the adapter's quirk handling:
fetch-then-verify ownership, GET-after-PUT, tenant post-filtering,
error mapping, network error wrapping.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from memcp.backend.mem0 import Mem0Backend
from memcp.types import MemoryAPIError

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
# entities — tenant post-filter
# ---------------------------------------------------------------------------


@respx.mock
async def test_entities_filters_by_user(backend):
    respx.get(f"{BASE}/entities").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "alice", "type": "user", "total_memories": 3},
                {"id": "bob", "type": "user", "total_memories": 5},
            ],
        )
    )
    result = await backend.entities(USER)
    assert len(result.entities) == 1
    assert result.entities[0]["id"] == "alice"


@respx.mock
async def test_entities_no_match_returns_empty(backend):
    respx.get(f"{BASE}/entities").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "bob", "type": "user", "total_memories": 5},
            ],
        )
    )
    result = await backend.entities(USER)
    assert len(result.entities) == 0


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
    respx.get(f"{BASE}/openapi.json").mock(return_value=httpx.Response(200, json={}))
    status = await backend.health()
    assert status.status == "healthy"


@respx.mock
async def test_health_unhealthy(backend):
    respx.get(f"{BASE}/openapi.json").mock(side_effect=httpx.ConnectError("down"))
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
