"""Unit tests for the export/import/migrate engine."""

from __future__ import annotations

import pytest

from memcp.backend.in_memory import InMemoryBackend
from memcp.migrate import (
    build_dedup_index,
    dedup_key,
    export_payload,
    import_payload,
    migrate,
    scope_key,
)

TENANT = "tenant_a"
OTHER = "tenant_b"


# ---------------------------------------------------------------------------
# Dedup identity
# ---------------------------------------------------------------------------


def test_scope_key_is_order_independent():
    forward = scope_key({"agent_id": "a", "run_id": "r"})
    reversed_ = scope_key({"run_id": "r", "agent_id": "a"})
    assert forward == reversed_


def test_scope_key_drops_none_values():
    assert scope_key({"agent_id": "a", "run_id": None}) == scope_key({"agent_id": "a"})


def test_scope_key_treats_empty_and_none_alike():
    assert scope_key(None) == scope_key({}) == ()


def test_scope_key_stringifies_values():
    assert scope_key({"run_id": 7}) == scope_key({"run_id": "7"})


def test_dedup_key_separates_scopes():
    assert dedup_key("x", {"agent_id": "a"}) != dedup_key("x", {"agent_id": "b"})
    assert dedup_key("x", {"agent_id": "a"}) == dedup_key("x", {"agent_id": "a"})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_export_returns_wire_shapes(backend: InMemoryBackend):
    await backend.add(TENANT, "exported fact", scope={"agent_id": "a"}, infer=False)
    payload = await export_payload(backend, TENANT)
    assert payload.count == 1
    assert payload.truncated is False
    entry = payload.memories[0]
    assert entry["content"] == "exported fact"
    assert entry["scope"] == {"agent_id": "a"}
    assert set(entry) == {
        "id",
        "content",
        "score",
        "scope",
        "metadata",
        "created_at",
        "updated_at",
    }


async def test_export_flags_truncation(backend: InMemoryBackend):
    for i in range(4):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT, limit=2)
    assert payload.count == 2
    assert payload.truncated is True


async def test_export_isolates_tenants(backend: InMemoryBackend):
    await backend.add(TENANT, "mine", infer=False)
    payload = await export_payload(backend, OTHER)
    assert payload.count == 0


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


async def test_dedup_index_keys_on_content_and_scope(backend: InMemoryBackend):
    await backend.add(TENANT, "same", scope={"agent_id": "a"}, infer=False)
    await backend.add(TENANT, "same", scope={"agent_id": "b"}, infer=False)
    index = await build_dedup_index(backend, TENANT)
    assert len(index) == 2


async def test_import_stores_verbatim(backend: InMemoryBackend):
    outcome = await import_payload(
        backend, TENANT, [{"content": "kept exactly", "metadata": {"k": "v"}}]
    )
    assert len(outcome.imported) == 1
    listing = await backend.list_memories(TENANT)
    assert listing.memories[0].content == "kept exactly"
    assert listing.memories[0].metadata == {"k": "v"}


async def test_import_rejects_bad_on_conflict(backend: InMemoryBackend):
    with pytest.raises(ValueError):
        await import_payload(backend, TENANT, [{"content": "x"}], on_conflict="merge")


async def test_import_overwrite_needs_update_capability(backend: InMemoryBackend, monkeypatch):
    monkeypatch.setattr(backend, "capabilities", lambda: {"list_memories"})
    with pytest.raises(ValueError, match="update_memory"):
        await import_payload(backend, TENANT, [{"content": "x"}], on_conflict="overwrite")


async def test_import_skips_same_content_same_scope(backend: InMemoryBackend):
    await backend.add(TENANT, "dup", scope={"agent_id": "a"}, infer=False)
    outcome = await import_payload(
        backend, TENANT, [{"content": "dup", "scope": {"agent_id": "a"}}]
    )
    assert not outcome.imported
    assert len(outcome.skipped) == 1


async def test_import_keeps_same_content_other_scope(backend: InMemoryBackend):
    await backend.add(TENANT, "dup", scope={"agent_id": "a"}, infer=False)
    outcome = await import_payload(
        backend, TENANT, [{"content": "dup", "scope": {"agent_id": "b"}}]
    )
    assert len(outcome.imported) == 1
    assert not outcome.skipped


async def test_import_duplicate_mode_skips_the_index_read(backend: InMemoryBackend, monkeypatch):
    await backend.add(TENANT, "dup", infer=False)

    async def fail(*args, **kwargs):
        raise AssertionError("on_conflict='duplicate' must not read the dedup index")

    monkeypatch.setattr(backend, "list_memories", fail)
    outcome = await import_payload(backend, TENANT, [{"content": "dup"}], on_conflict="duplicate")
    assert len(outcome.imported) == 1


async def test_import_reports_per_entry_errors(backend: InMemoryBackend):
    outcome = await import_payload(
        backend, TENANT, [{"content": "ok"}, {"content": ""}, {"nope": 1}]
    )
    assert len(outcome.imported) == 1
    assert [e["index"] for e in outcome.errors] == [1, 2]


async def test_import_scope_validator_rejects_one_entry(backend: InMemoryBackend):
    def validator(scope):
        if "bad" in scope:
            raise ValueError("unknown scope key")
        return scope

    outcome = await import_payload(
        backend,
        TENANT,
        [{"content": "good", "scope": {"agent_id": "a"}}, {"content": "bad", "scope": {"bad": 1}}],
        scope_validator=validator,
    )
    assert len(outcome.imported) == 1
    assert outcome.errors == [{"index": 1, "error": "unknown scope key"}]


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


async def test_migrate_between_backends():
    source, target = InMemoryBackend(), InMemoryBackend()
    for i in range(3):
        await source.add(TENANT, f"fact {i}", scope={"agent_id": "a"}, infer=False)
    report = await migrate(source, target, TENANT, source_name="src", target_name="dst")
    assert (report.exported, report.imported, report.skipped) == (3, 3, 0)
    assert report.errors == []
    landed = await target.list_memories(TENANT)
    assert {m.content for m in landed.memories} == {"fact 0", "fact 1", "fact 2"}


async def test_migrate_into_a_different_tenant():
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add(TENANT, "carried across", infer=False)
    report = await migrate(source, target, TENANT, target_user_id=OTHER)
    assert report.imported == 1
    assert (await target.list_memories(TENANT)).memories == []
    assert (await target.list_memories(OTHER)).memories[0].content == "carried across"


async def test_migrate_preserves_scope_across_duplicated_content():
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add(TENANT, "shared", scope={"agent_id": "a"}, infer=False)
    await source.add(TENANT, "shared", scope={"agent_id": "b"}, infer=False)
    report = await migrate(source, target, TENANT)
    assert report.imported == 2
    landed = await target.list_memories(TENANT)
    assert {m.scope["agent_id"] for m in landed.memories} == {"a", "b"}


async def test_migrate_is_idempotent_on_a_second_run():
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add(TENANT, "once", infer=False)
    await migrate(source, target, TENANT)
    second = await migrate(source, target, TENANT)
    assert (second.imported, second.skipped) == (0, 1)
    assert len((await target.list_memories(TENANT)).memories) == 1
