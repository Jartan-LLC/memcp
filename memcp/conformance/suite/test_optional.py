"""Capability-gated conformance (A2).

Every test carries `@pytest.mark.conformance(<capability>)` and skips only when the
backend does not declare that capability. A backend that declares one and fails a
test here fails the run — it does not skip.
"""

from __future__ import annotations

import pytest

from memcp.backend.base import MemoryBackend
from memcp.types import AddResult, EntitiesResult, ListResult, MemoryAPIError

MISSING_ID = "00000000-0000-4000-8000-000000000000"


def _first_id(result: AddResult | list[AddResult]) -> str:
    items = result if isinstance(result, list) else [result]
    assert items, "add() stored nothing"
    return items[0].id


def _require(backend: MemoryBackend, capability: str) -> None:
    if capability not in backend.capabilities():
        pytest.skip("not declared in capabilities()")


# ---------------------------------------------------------------------------
# get_memory
# ---------------------------------------------------------------------------


@pytest.mark.conformance("get_memory")
async def test_get_returns_the_memory(backend: MemoryBackend, tenant: str):
    _require(backend, "get_memory")
    content = "conformance get returns full content"
    memory_id = _first_id(await backend.add(tenant, content, infer=False))
    memory = await backend.get(tenant, memory_id)
    assert memory is not None
    assert memory.id == memory_id
    assert memory.content == content


@pytest.mark.conformance("get_memory")
async def test_get_missing_returns_none(backend: MemoryBackend, tenant: str):
    _require(backend, "get_memory")
    assert await backend.get(tenant, MISSING_ID) is None


@pytest.mark.conformance("get_memory")
async def test_get_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "get_memory")
    memory_id = _first_id(await backend.add(tenant, "conformance get isolation", infer=False))
    assert await backend.get(other_tenant, memory_id) is None, (
        "get() returned another tenant's memory"
    )


@pytest.mark.conformance("get_memory")
async def test_get_preserves_scope_and_metadata(backend: MemoryBackend, tenant: str):
    _require(backend, "get_memory")
    scope_key = backend.scope_keys()[0]
    scope = {scope_key: "conformance_get_scope"}
    metadata = {"source": "conformance", "revision": "3"}
    memory_id = _first_id(
        await backend.add(
            tenant,
            "conformance scope and metadata survive a write",
            scope=scope,
            metadata=metadata,
            infer=False,
        )
    )
    memory = await backend.get(tenant, memory_id)
    assert memory is not None
    assert memory.scope.get(scope_key) == scope[scope_key], (
        f"scope key {scope_key!r} did not survive add() -> get(): {memory.scope}"
    )
    assert memory.metadata == metadata, (
        f"metadata did not survive add() -> get(): {memory.metadata}"
    )


# ---------------------------------------------------------------------------
# update_memory
# ---------------------------------------------------------------------------


@pytest.mark.conformance("update_memory")
async def test_update_replaces_content(backend: MemoryBackend, tenant: str):
    _require(backend, "update_memory")
    memory_id = _first_id(await backend.add(tenant, "conformance before update", infer=False))
    updated = await backend.update(tenant, memory_id, "conformance after update")
    assert updated.content == "conformance after update"
    assert updated.id == memory_id


@pytest.mark.conformance("update_memory")
async def test_update_missing_raises_404(backend: MemoryBackend, tenant: str):
    _require(backend, "update_memory")
    with pytest.raises(MemoryAPIError) as exc:
        await backend.update(tenant, MISSING_ID, "conformance no such memory")
    assert exc.value.status == 404


@pytest.mark.conformance("update_memory")
async def test_update_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "update_memory")
    memory_id = _first_id(await backend.add(tenant, "conformance update isolation", infer=False))
    with pytest.raises(MemoryAPIError) as exc:
        await backend.update(other_tenant, memory_id, "conformance hijacked")
    assert exc.value.status == 404


# ---------------------------------------------------------------------------
# list_memories
# ---------------------------------------------------------------------------


@pytest.mark.conformance("list_memories")
async def test_list_returns_added_memories(backend: MemoryBackend, tenant: str):
    _require(backend, "list_memories")
    contents = {f"conformance list entry {i}" for i in range(3)}
    for content in sorted(contents):
        await backend.add(tenant, content, infer=False)
    result = await backend.list_memories(tenant, limit=100)
    assert isinstance(result, ListResult)
    assert contents <= {m.content for m in result.memories}


@pytest.mark.conformance("list_memories")
async def test_list_filters_by_scope(backend: MemoryBackend, tenant: str):
    _require(backend, "list_memories")
    scope_key = backend.scope_keys()[0]
    wanted = {scope_key: "conformance_list_wanted"}
    other = {scope_key: "conformance_list_other"}
    await backend.add(tenant, "conformance in the wanted scope", scope=wanted, infer=False)
    await backend.add(tenant, "conformance in the other scope", scope=other, infer=False)

    result = await backend.list_memories(tenant, scope=wanted, limit=100)
    contents = {m.content for m in result.memories}
    assert "conformance in the wanted scope" in contents
    assert "conformance in the other scope" not in contents


@pytest.mark.conformance("list_memories")
async def test_list_paginates(backend: MemoryBackend, tenant: str):
    _require(backend, "list_memories")
    for i in range(5):
        await backend.add(tenant, f"conformance pagination entry {i}", infer=False)
    first = await backend.list_memories(tenant, limit=2)
    assert len(first.memories) == 2
    assert first.next_cursor is not None, "a partial page must carry a next_cursor"
    second = await backend.list_memories(tenant, limit=2, cursor=first.next_cursor)
    assert len(second.memories) == 2
    assert {m.id for m in first.memories}.isdisjoint({m.id for m in second.memories}), (
        "the second page repeated memories from the first"
    )


@pytest.mark.conformance("list_memories")
async def test_list_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "list_memories")
    await backend.add(tenant, "conformance list isolation marker", infer=False)
    result = await backend.list_memories(other_tenant, limit=100)
    assert "conformance list isolation marker" not in {m.content for m in result.memories}


# ---------------------------------------------------------------------------
# memory_history
# ---------------------------------------------------------------------------


@pytest.mark.conformance("memory_history")
async def test_history_records_creation(backend: MemoryBackend, tenant: str):
    _require(backend, "memory_history")
    memory_id = _first_id(await backend.add(tenant, "conformance history subject", infer=False))
    entries = await backend.history(tenant, memory_id)
    assert entries, "history() returned nothing for a memory that was just created"
    assert all(e.action and isinstance(e.action, str) for e in entries), (
        "every history entry needs an action string; the vocabulary is backend-specific"
    )


@pytest.mark.conformance("memory_history")
async def test_history_records_an_update(backend: MemoryBackend, tenant: str):
    _require(backend, "memory_history")
    if "update_memory" not in backend.capabilities():
        pytest.skip("needs update_memory to produce a second history entry")
    memory_id = _first_id(await backend.add(tenant, "conformance history original", infer=False))
    before = len(await backend.history(tenant, memory_id))
    await backend.update(tenant, memory_id, "conformance history revised")
    after = await backend.history(tenant, memory_id)
    assert len(after) > before, "update() did not add a history entry"
    assert any(e.content_after == "conformance history revised" for e in after), (
        f"no history entry carries the new content: {[e.content_after for e in after]}"
    )


@pytest.mark.conformance("memory_history")
async def test_history_missing_raises_404(backend: MemoryBackend, tenant: str):
    _require(backend, "memory_history")
    with pytest.raises(MemoryAPIError) as exc:
        await backend.history(tenant, MISSING_ID)
    assert exc.value.status == 404


@pytest.mark.conformance("memory_history")
async def test_history_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "memory_history")
    memory_id = _first_id(await backend.add(tenant, "conformance history isolation", infer=False))
    with pytest.raises(MemoryAPIError) as exc:
        await backend.history(other_tenant, memory_id)
    assert exc.value.status == 404


# ---------------------------------------------------------------------------
# memory_entities
# ---------------------------------------------------------------------------


@pytest.mark.conformance("memory_entities")
async def test_entities_shape(backend: MemoryBackend, tenant: str):
    _require(backend, "memory_entities")
    await backend.add(tenant, "Ada Lovelace wrote the first algorithm", infer=False)
    result = await backend.entities(tenant)
    assert isinstance(result, EntitiesResult)
    assert isinstance(result.entities, list)
    assert isinstance(result.relationships, list)
    assert all(isinstance(e, dict) for e in result.entities)
    assert all(isinstance(r, dict) for r in result.relationships)


@pytest.mark.conformance("memory_entities")
async def test_entities_reflect_stored_memories(backend: MemoryBackend, tenant: str):
    _require(backend, "memory_entities")
    empty = await backend.entities(tenant)
    assert not empty.entities, "a tenant with no memories should have no entities"
    await backend.add(tenant, "Grace Hopper built the first compiler", infer=False)
    populated = await backend.entities(tenant)
    assert populated.entities, "entities() returned nothing for a tenant that has memories"


@pytest.mark.conformance("memory_entities")
async def test_entities_isolate_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "memory_entities")
    await backend.add(tenant, "conformance entity isolation own tenant", infer=False)
    await backend.add(other_tenant, "conformance entity isolation other tenant", infer=False)
    mine = await backend.entities(tenant)
    identifiers = {e.get("id") for e in mine.entities}
    assert other_tenant not in identifiers, "entities() leaked another tenant"
