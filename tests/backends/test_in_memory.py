"""In-memory backend conformance tests.

These tests define the contract any MemoryBackend must satisfy.
"""

from __future__ import annotations

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.types import MemoryAPIError

USER_A = "alice"
USER_B = "bob"

# ---------------------------------------------------------------------------
# Add + Search
# ---------------------------------------------------------------------------


async def test_add_returns_result(backend: InMemoryBackend):
    results = await backend.add(USER_A, "I like Python")
    assert len(results) == 1
    assert results[0].id
    assert results[0].status == "ready"


async def test_search_finds_added_memory(backend: InMemoryBackend):
    await backend.add(USER_A, "My favorite language is Python")
    results = await backend.search(USER_A, "Python")
    assert len(results) >= 1
    assert any("Python" in m.content for m in results)


async def test_search_empty_query_returns_nothing(backend: InMemoryBackend):
    await backend.add(USER_A, "I like Python")
    results = await backend.search(USER_A, "zzzznonexistent")
    assert results == []


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_tenant_isolation_search(backend: InMemoryBackend):
    """add(user_a) → search(user_b) → zero results."""
    await backend.add(USER_A, "I like cats")
    results = await backend.search(USER_B, "cats")
    assert results == []


async def test_tenant_isolation_get(backend: InMemoryBackend):
    """add(user_a) → get(user_b, same id) → None."""
    added = await backend.add(USER_A, "secret data")
    memory_id = added[0].id
    result = await backend.get(USER_B, memory_id)
    assert result is None


async def test_tenant_isolation_delete(backend: InMemoryBackend):
    """add(user_a) → delete(user_b) → error, memory still exists for user_a."""
    added = await backend.add(USER_A, "important data")
    memory_id = added[0].id
    with pytest.raises(MemoryAPIError):
        await backend.delete(USER_B, memory_id)
    result = await backend.get(USER_A, memory_id)
    assert result is not None


async def test_tenant_isolation_delete_all(backend: InMemoryBackend):
    """delete_all(user_a) does not affect user_b."""
    await backend.add(USER_A, "alice memory", scope={"agent_id": "test"})
    await backend.add(USER_B, "bob memory", scope={"agent_id": "test"})
    count = await backend.delete_all(USER_A, {"agent_id": "test"})
    assert count == 1
    results = await backend.search(USER_B, "bob memory")
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_removes_memory(backend: InMemoryBackend):
    added = await backend.add(USER_A, "to be deleted")
    memory_id = added[0].id
    result = await backend.delete(USER_A, memory_id)
    assert result is True
    get_result = await backend.get(USER_A, memory_id)
    assert get_result is None


async def test_delete_nonexistent_raises(backend: InMemoryBackend):
    with pytest.raises(MemoryAPIError):
        await backend.delete(USER_A, "nonexistent-id")


# ---------------------------------------------------------------------------
# Delete all with scope
# ---------------------------------------------------------------------------


async def test_delete_all_scoped(backend: InMemoryBackend):
    await backend.add(USER_A, "scoped memory", scope={"agent_id": "agent1"})
    await backend.add(USER_A, "other memory", scope={"agent_id": "agent2"})
    count = await backend.delete_all(USER_A, {"agent_id": "agent1"})
    assert count == 1
    listing = await backend.list_memories(USER_A)
    assert len(listing.memories) == 1
    assert listing.memories[0].content == "other memory"


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


async def test_get_existing(backend: InMemoryBackend):
    added = await backend.add(USER_A, "fetch me")
    memory_id = added[0].id
    result = await backend.get(USER_A, memory_id)
    assert result is not None
    assert result.content == "fetch me"
    assert result.id == memory_id


async def test_get_nonexistent(backend: InMemoryBackend):
    result = await backend.get(USER_A, "nonexistent-id")
    assert result is None


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_changes_content(backend: InMemoryBackend):
    added = await backend.add(USER_A, "original")
    memory_id = added[0].id
    updated = await backend.update(USER_A, memory_id, "modified")
    assert updated.content == "modified"
    assert updated.updated_at is not None
    fetched = await backend.get(USER_A, memory_id)
    assert fetched is not None
    assert fetched.content == "modified"


async def test_update_nonexistent_raises(backend: InMemoryBackend):
    with pytest.raises(MemoryAPIError):
        await backend.update(USER_A, "nonexistent", "new content")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_memories(backend: InMemoryBackend):
    await backend.add(USER_A, "memory one")
    await backend.add(USER_A, "memory two")
    listing = await backend.list_memories(USER_A)
    assert len(listing.memories) == 2


async def test_list_memories_scoped(backend: InMemoryBackend):
    await backend.add(USER_A, "scoped", scope={"agent_id": "a1"})
    await backend.add(USER_A, "unscoped")
    listing = await backend.list_memories(USER_A, scope={"agent_id": "a1"})
    assert len(listing.memories) == 1
    assert listing.memories[0].content == "scoped"


async def test_list_memories_pagination(backend: InMemoryBackend):
    for i in range(5):
        await backend.add(USER_A, f"memory {i}")
    page1 = await backend.list_memories(USER_A, limit=2)
    assert len(page1.memories) == 2
    assert page1.next_cursor is not None
    page2 = await backend.list_memories(USER_A, limit=2, cursor=page1.next_cursor)
    assert len(page2.memories) == 2
    page1_ids = {m.id for m in page1.memories}
    page2_ids = {m.id for m in page2.memories}
    assert page1_ids.isdisjoint(page2_ids), "Pages should not overlap"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def test_history_tracks_changes(backend: InMemoryBackend):
    added = await backend.add(USER_A, "original")
    memory_id = added[0].id
    await backend.update(USER_A, memory_id, "updated")
    entries = await backend.history(USER_A, memory_id)
    assert len(entries) == 2
    assert entries[0].action == "created"
    assert entries[1].action == "updated"
    assert entries[1].content_before == "original"
    assert entries[1].content_after == "updated"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health(backend: InMemoryBackend):
    status = await backend.health()
    assert status.status == "healthy"
    assert status.backend == "in_memory"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


async def test_capabilities_are_valid(backend: InMemoryBackend):
    caps = backend.capabilities()
    valid = {"update", "list", "history", "entities"}
    assert caps <= valid


async def test_scope_keys_returns_list(backend: InMemoryBackend):
    keys = backend.scope_keys()
    assert isinstance(keys, list)
    assert len(keys) > 0


# ---------------------------------------------------------------------------
# Scope handling
# ---------------------------------------------------------------------------


async def test_search_with_scope_narrows_results(backend: InMemoryBackend):
    await backend.add(USER_A, "agent1 memory", scope={"agent_id": "a1"})
    await backend.add(USER_A, "agent2 memory", scope={"agent_id": "a2"})
    results = await backend.search(USER_A, "memory", scope={"agent_id": "a1"})
    assert len(results) == 1
    assert "agent1" in results[0].content


def test_declares_keyword_retrieval_and_no_list_ceiling(backend: InMemoryBackend):
    assert (backend.retrieval, backend.list_ceiling) == ("keyword", None)
