"""Capability-gated conformance.

Every test carries `@pytest.mark.conformance(<capability>)` and skips only when the
backend does not declare that capability. A backend that declares one and fails a
test here fails the run — it does not skip.
"""

from __future__ import annotations

import pytest

from memcp.backends.base import MemoryBackend, MetadataPatch
from memcp.types import AddResult, EntitiesResult, ListResult, MemoryAPIError, System

MISSING_ID = "00000000-0000-4000-8000-000000000000"


def _first_id(result: AddResult | list[AddResult]) -> str:
    items = result if isinstance(result, list) else [result]
    assert items, "add() stored nothing"
    return items[0].id


def _require(backend: MemoryBackend, capability: str) -> None:
    if capability not in backend.capabilities():
        pytest.skip("not declared in capabilities()")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@pytest.mark.conformance("update")
async def test_update_replaces_content(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    memory_id = _first_id(await backend.add(tenant, "conformance before update", infer=False))
    updated = await backend.update(tenant, memory_id, "conformance after update")
    assert updated.content == "conformance after update"
    assert updated.id == memory_id


@pytest.mark.conformance("update")
async def test_update_stores_the_system_it_is_given(backend: MemoryBackend, tenant: str):
    created: System = {"space": tenant, "author": "writer-a", "created_by": "writer-a"}
    changed: System = {"space": f"stored-{tenant}", "author": "writer-b", "created_by": "writer-a"}
    _require(backend, "update")
    memory_id = _first_id(
        await backend.add(tenant, "conformance system before", system=created, infer=False)
    )
    updated = await backend.update(tenant, memory_id, "conformance system after", system=changed)
    assert (updated.partition, updated.system) == (tenant, changed)
    got = await backend.get(tenant, memory_id)
    assert got is not None
    assert got.system == changed


@pytest.mark.conformance("update")
async def test_update_without_system_keeps_the_stored_one(backend: MemoryBackend, tenant: str):
    created: System = {"space": tenant, "author": "writer-a", "created_by": "writer-a"}
    _require(backend, "update")
    memory_id = _first_id(
        await backend.add(tenant, "conformance system kept", system=created, infer=False)
    )
    updated = await backend.update(tenant, memory_id, "conformance system still kept")
    assert updated.system == created


async def _with_metadata(backend: MemoryBackend, tenant: str, metadata: dict[str, str]) -> str:
    content = "conformance metadata patch subject"
    return _first_id(await backend.add(tenant, content, metadata=metadata, infer=False))


@pytest.mark.conformance("update")
async def test_update_patch_keeps_the_keys_it_does_not_name(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"kept": "a", "changed": "b"})
    await backend.update(tenant, memory_id, patch=MetadataPatch(set={"changed": "c"}))
    got = await backend.get(tenant, memory_id)
    assert got is not None
    assert got.metadata == {"kept": "a", "changed": "c"}


@pytest.mark.conformance("update")
async def test_update_patch_removes_a_key_from_every_read(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"kept": "a", "gone": "b"})
    await backend.update(tenant, memory_id, patch=MetadataPatch(remove=frozenset({"gone"})))
    got = await backend.get(tenant, memory_id)
    assert got is not None
    hits = await backend.search(tenant, "conformance metadata patch subject", limit=10)
    reads = [got, *(m for m in hits if m.id == memory_id)]
    if "list" in backend.capabilities():
        listed = await backend.list_memories(tenant)
        reads += [m for m in listed.memories if m.id == memory_id]
    assert len(reads) >= 2, "search didn't find the patched memory"
    assert [m.metadata for m in reads] == [{"kept": "a"}] * len(reads)


@pytest.mark.conformance("update")
async def test_update_removing_a_missing_key_does_nothing(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"kept": "a"})
    updated = await backend.update(
        tenant, memory_id, patch=MetadataPatch(remove=frozenset({"never-there"}))
    )
    assert updated.metadata == {"kept": "a"}


@pytest.mark.conformance("update")
async def test_a_content_only_update_keeps_the_metadata(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"kept": "a"})
    updated = await backend.update(tenant, memory_id, "conformance new text only")
    assert (updated.content, updated.metadata) == ("conformance new text only", {"kept": "a"})


@pytest.mark.conformance("update")
async def test_a_metadata_only_update_keeps_the_content_and_its_search_hit(
    backend: MemoryBackend, tenant: str
):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"kept": "a"})
    updated = await backend.update(tenant, memory_id, patch=MetadataPatch(set={"added": "b"}))
    assert updated.content == "conformance metadata patch subject"
    hits = await backend.search(tenant, "conformance metadata patch subject", limit=10)
    assert memory_id in [m.id for m in hits], "a metadata-only update lost the search hit"


@pytest.mark.conformance("update")
async def test_a_removed_key_can_be_set_again(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"k": "first"})
    await backend.update(tenant, memory_id, patch=MetadataPatch(remove=frozenset({"k"})))
    updated = await backend.update(tenant, memory_id, patch=MetadataPatch(set={"k": "second"}))
    got = await backend.get(tenant, memory_id)
    assert got is not None
    assert updated.metadata == got.metadata == {"k": "second"}


@pytest.mark.conformance("update")
@pytest.mark.parametrize(
    "patch",
    [MetadataPatch(set={"role": "x"}), MetadataPatch(remove=frozenset({"_memcp_system"}))],
    ids=["set-reserved", "remove-memcp"],
)
async def test_update_refuses_a_reserved_metadata_key(
    backend: MemoryBackend, tenant: str, patch: MetadataPatch
):
    _require(backend, "update")
    memory_id = await _with_metadata(backend, tenant, {"kept": "a"})
    with pytest.raises(MemoryAPIError) as refused:
        await backend.update(tenant, memory_id, patch=patch)
    assert refused.value.status == 400
    got = await backend.get(tenant, memory_id)
    assert got is not None
    assert got.metadata == {"kept": "a"}


@pytest.mark.conformance("update")
async def test_update_missing_raises_404(backend: MemoryBackend, tenant: str):
    _require(backend, "update")
    with pytest.raises(MemoryAPIError) as exc:
        await backend.update(tenant, MISSING_ID, "conformance no such memory")
    assert exc.value.status == 404


@pytest.mark.conformance("update")
async def test_update_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "update")
    memory_id = _first_id(await backend.add(tenant, "conformance update isolation", infer=False))
    with pytest.raises(MemoryAPIError) as exc:
        await backend.update(other_tenant, memory_id, "conformance hijacked")
    assert exc.value.status == 404


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.conformance("list")
async def test_list_returns_added_memories(backend: MemoryBackend, tenant: str):
    _require(backend, "list")
    contents = {f"conformance list entry {i}" for i in range(3)}
    for content in sorted(contents):
        await backend.add(tenant, content, infer=False)
    result = await backend.list_memories(tenant, limit=100)
    assert isinstance(result, ListResult)
    assert contents <= {m.content for m in result.memories}


@pytest.mark.conformance("list")
async def test_list_filters_by_scope(backend: MemoryBackend, tenant: str):
    _require(backend, "list")
    scope_key = backend.scope_keys()[0]
    wanted = {scope_key: "conformance_list_wanted"}
    other = {scope_key: "conformance_list_other"}
    await backend.add(tenant, "conformance in the wanted scope", scope=wanted, infer=False)
    await backend.add(tenant, "conformance in the other scope", scope=other, infer=False)

    result = await backend.list_memories(tenant, scope=wanted, limit=100)
    contents = {m.content for m in result.memories}
    assert "conformance in the wanted scope" in contents
    assert "conformance in the other scope" not in contents


@pytest.mark.conformance("list")
async def test_list_paginates(backend: MemoryBackend, tenant: str):
    _require(backend, "list")
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


@pytest.mark.conformance("list")
async def test_list_returns_the_stored_scope(backend: MemoryBackend, tenant: str):
    """Policy drops a result whose scope doesn't match the request, so the scope must come back."""
    _require(backend, "list")
    scope = {key: f"listed_{key}" for key in backend.scope_keys()}
    await backend.add(tenant, "list scope round trip", scope=scope, infer=False)
    result = await backend.list_memories(tenant, scope=scope)
    assert [{k: str(m.scope.get(k)) for k in scope} for m in result.memories] == [scope]


@pytest.mark.conformance("list")
async def test_list_returns_the_partition_and_system(backend: MemoryBackend, tenant: str):
    _require(backend, "list")
    system: System = {"space": tenant, "author": "lister", "created_by": "lister"}
    await backend.add(tenant, "conformance listed stamp", system=system, infer=False)
    result = await backend.list_memories(tenant)
    assert [(m.partition, m.system) for m in result.memories] == [(tenant, system)]


@pytest.mark.conformance("list")
async def test_list_cursors_reach_every_memory_once(backend: MemoryBackend, tenant: str):
    _require(backend, "list")
    added = set()
    for i in range(5):
        result = await backend.add(tenant, f"conformance cursor entry {i}", infer=False)
        added.update(r.id for r in (result if isinstance(result, list) else [result]))
    seen: list[str] = []
    cursor = None
    while True:
        page = await backend.list_memories(tenant, limit=2, cursor=cursor)
        assert page.incomplete is False, "a short list must not be marked incomplete"
        seen.extend(m.id for m in page.memories)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert sorted(seen) == sorted(added), "following next_cursor must return each memory once"


@pytest.mark.conformance("list")
async def test_list_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "list")
    await backend.add(tenant, "conformance list isolation marker", infer=False)
    result = await backend.list_memories(other_tenant, limit=100)
    assert "conformance list isolation marker" not in {m.content for m in result.memories}


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@pytest.mark.conformance("history")
async def test_history_records_creation(backend: MemoryBackend, tenant: str):
    _require(backend, "history")
    memory_id = _first_id(await backend.add(tenant, "conformance history subject", infer=False))
    entries = await backend.history(tenant, memory_id)
    assert entries, "history() returned nothing for a memory that was just created"
    assert all(e.action and isinstance(e.action, str) for e in entries), (
        "every history entry needs an action string; the vocabulary is backend-specific"
    )


@pytest.mark.conformance("history")
async def test_history_records_an_update(backend: MemoryBackend, tenant: str):
    _require(backend, "history")
    if "update" not in backend.capabilities():
        pytest.skip("needs update to produce a second history entry")
    memory_id = _first_id(await backend.add(tenant, "conformance history original", infer=False))
    before = len(await backend.history(tenant, memory_id))
    await backend.update(tenant, memory_id, "conformance history revised")
    after = await backend.history(tenant, memory_id)
    assert len(after) > before, "update() did not add a history entry"
    assert any(e.content_after == "conformance history revised" for e in after), (
        f"no history entry carries the new content: {[e.content_after for e in after]}"
    )


@pytest.mark.conformance("history")
async def test_history_records_a_metadata_only_update_without_changing_the_text(
    backend: MemoryBackend, tenant: str
):
    _require(backend, "history")
    if "update" not in backend.capabilities():
        pytest.skip("needs update to produce a second history entry")
    original = "conformance history metadata only"
    memory_id = _first_id(await backend.add(tenant, original, infer=False))
    before = len(await backend.history(tenant, memory_id))
    await backend.update(tenant, memory_id, patch=MetadataPatch(set={"k": "v"}))
    after = await backend.history(tenant, memory_id)
    assert len(after) > before, "a metadata-only update added no history entry"
    assert after[-1].content_after == original, [after[-1].content_before, after[-1].content_after]


@pytest.mark.conformance("history")
async def test_history_missing_raises_404(backend: MemoryBackend, tenant: str):
    _require(backend, "history")
    with pytest.raises(MemoryAPIError) as exc:
        await backend.history(tenant, MISSING_ID)
    assert exc.value.status == 404


@pytest.mark.conformance("history")
async def test_history_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "history")
    memory_id = _first_id(await backend.add(tenant, "conformance history isolation", infer=False))
    with pytest.raises(MemoryAPIError) as exc:
        await backend.history(other_tenant, memory_id)
    assert exc.value.status == 404


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


@pytest.mark.conformance("entities")
async def test_entities_shape(backend: MemoryBackend, tenant: str):
    _require(backend, "entities")
    await backend.add(tenant, "Ada Lovelace wrote the first algorithm", infer=False)
    result = await backend.entities(tenant)
    assert isinstance(result, EntitiesResult)
    assert isinstance(result.entities, list)
    assert isinstance(result.relationships, list)
    assert all(isinstance(e, dict) for e in result.entities)
    assert all(isinstance(r, dict) for r in result.relationships)


@pytest.mark.conformance("entities")
async def test_entities_reflect_stored_memories(backend: MemoryBackend, tenant: str):
    _require(backend, "entities")
    empty = await backend.entities(tenant)
    assert not empty.entities, "a tenant with no memories should have no entities"
    await backend.add(tenant, "Grace Hopper built the first compiler", infer=False)
    populated = await backend.entities(tenant)
    assert populated.entities, "entities() returned nothing for a tenant that has memories"


@pytest.mark.conformance("entities")
async def test_entities_isolate_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    _require(backend, "entities")
    await backend.add(tenant, "conformance entity isolation own tenant", infer=False)
    await backend.add(other_tenant, "conformance entity isolation other tenant", infer=False)
    mine = await backend.entities(tenant)
    identifiers = {e.get("id") for e in mine.entities}
    assert other_tenant not in identifiers, "entities() leaked another tenant"
