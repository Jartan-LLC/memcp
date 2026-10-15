"""Unit tests for the export/import/migrate engine."""

from __future__ import annotations

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.policy.transfer import (
    build_dedup_index,
    dedup_key,
    export_payload,
    import_payload,
    migrate,
    scope_key,
)
from memcp.types import ListResult, Memory, MemoryAPIError, PolicyError, System

TENANT = "tenant_a"
OTHER = "tenant_b"


class PagedBackend(InMemoryBackend):
    """Returns at most `page_size` memories per call, and can mark its pages incomplete.

    Stands in for a store with a server-side page size, or (with `incomplete=True`) one
    with a list ceiling it can't page past, like mem0.
    """

    def __init__(self, page_size: int, *, incomplete: bool = False) -> None:
        super().__init__()
        self.page_size = page_size
        self.incomplete = incomplete

    async def list_memories(self, partition, *, scope=None, limit=100, cursor=None) -> ListResult:
        page = await super().list_memories(
            partition, scope=scope, limit=min(limit, self.page_size), cursor=cursor
        )
        page.incomplete = self.incomplete_on(cursor)
        return page

    def incomplete_on(self, cursor: str | None) -> bool:
        return self.incomplete


class FirstPageIncompleteBackend(PagedBackend):
    """Marks only the first page incomplete, so a later page can't clear the mark."""

    def incomplete_on(self, cursor: str | None) -> bool:
        return cursor is None


class EmptyPageBackend(InMemoryBackend):
    """Returns an empty page that still carries a cursor, and fails rather than loop."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def list_memories(self, partition, *, scope=None, limit=100, cursor=None) -> ListResult:
        self.calls += 1
        if self.calls > 3:
            raise AssertionError("a reader kept following a cursor past an empty page")
        return ListResult(memories=[], next_cursor="more")


class ForeignPartitionBackend(InMemoryBackend):
    """Lists the requested tenant's memories as though they were stored under another."""

    async def list_memories(self, partition, *, scope=None, limit=100, cursor=None) -> ListResult:
        page = await super().list_memories(partition, scope=scope, limit=limit, cursor=cursor)
        page.memories = [
            Memory(id=m.id, content=m.content, partition="elsewhere", system=m.system)
            for m in page.memories
        ]
        return page


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


def test_scope_key_uses_the_stored_text():
    assert scope_key({"run_id": 7}) == scope_key({"run_id": "7"})
    assert scope_key({"run_id": True}) == scope_key({"run_id": "true"})


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
        "author",
    }


async def test_export_at_exactly_the_limit_is_complete(backend: InMemoryBackend):
    for i in range(3):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT, limit=3)
    assert (payload.count, payload.truncated) == (3, False)


async def test_export_at_exactly_the_limit_across_pages_is_complete():
    backend = PagedBackend(page_size=2)
    for i in range(4):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT, limit=4)
    assert (payload.count, payload.truncated) == (4, False)


async def test_export_flags_truncation(backend: InMemoryBackend):
    for i in range(4):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT, limit=2)
    assert payload.count == 2
    assert payload.truncated is True


async def test_export_follows_next_cursor():
    backend = PagedBackend(page_size=2)
    for i in range(5):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT)
    assert payload.count == 5
    assert payload.truncated is False
    assert {m["content"] for m in payload.memories} == {f"fact {i}" for i in range(5)}


async def test_export_flags_truncation_across_pages():
    backend = PagedBackend(page_size=2)
    for i in range(5):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT, limit=3)
    assert payload.count == 3
    assert payload.truncated is True


async def test_export_flags_an_incomplete_list():
    backend = PagedBackend(page_size=100, incomplete=True)
    for i in range(3):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT)
    assert payload.count == 3
    assert payload.truncated is True


async def test_export_keeps_an_incomplete_mark_from_an_earlier_page():
    backend = FirstPageIncompleteBackend(page_size=2)
    for i in range(5):
        await backend.add(TENANT, f"fact {i}", infer=False)
    payload = await export_payload(backend, TENANT)
    assert payload.count == 5
    assert payload.truncated is True


async def test_export_flags_an_empty_page_that_still_has_a_cursor():
    payload = await export_payload(EmptyPageBackend(), TENANT)
    assert payload.count == 0
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
    index, incomplete = await build_dedup_index(backend, TENANT)
    assert len(index) == 2
    assert incomplete is False


async def test_dedup_index_follows_next_cursor():
    backend = PagedBackend(page_size=2)
    for i in range(5):
        await backend.add(TENANT, f"fact {i}", infer=False)
    index, incomplete = await build_dedup_index(backend, TENANT)
    assert len(index) == 5
    assert incomplete is False


async def test_dedup_index_flags_the_cap():
    backend = PagedBackend(page_size=2)
    for i in range(5):
        await backend.add(TENANT, f"fact {i}", infer=False)
    index, incomplete = await build_dedup_index(backend, TENANT, limit=3)
    assert len(index) == 3
    assert incomplete is True


async def test_import_skips_a_duplicate_beyond_the_first_page():
    backend = PagedBackend(page_size=2)
    for i in range(5):
        await backend.add(TENANT, f"fact {i}", infer=False)
    outcome = await import_payload(backend, TENANT, [{"content": "fact 4"}])
    assert len(outcome.skipped) == 1
    assert outcome.imported == []


async def test_import_skips_a_duplicate_with_a_boolean_scope(backend: InMemoryBackend):
    entry = {"content": "flagged fact", "scope": {"run_id": True}}
    await import_payload(backend, TENANT, [entry])
    outcome = await import_payload(backend, TENANT, [entry])
    assert len(outcome.skipped) == 1
    assert outcome.imported == []


async def test_import_reports_a_complete_dedup_read(backend: InMemoryBackend):
    outcome = await import_payload(backend, TENANT, [{"content": "x"}])
    assert outcome.dedup_incomplete is False


async def test_import_duplicate_mode_leaves_dedup_incomplete_false():
    backend = PagedBackend(page_size=100, incomplete=True)
    outcome = await import_payload(backend, TENANT, [{"content": "x"}], on_conflict="duplicate")
    assert outcome.dedup_incomplete is False


async def test_import_reports_an_incomplete_dedup_read():
    backend = PagedBackend(page_size=100, incomplete=True)
    outcome = await import_payload(backend, TENANT, [{"content": "x"}])
    assert outcome.dedup_incomplete is True
    assert len(outcome.imported) == 1


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
    monkeypatch.setattr(backend, "capabilities", lambda: {"list"})
    with pytest.raises(ValueError, match="update capability"):
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


@pytest.mark.parametrize("on_conflict", ["duplicate", "overwrite"])
async def test_import_keeps_the_store_response_out_of_an_entry_error(
    backend: InMemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    on_conflict: str,
):
    await backend.add(TENANT, "x", infer=False)

    async def failing(*args, **kwargs):
        raise MemoryAPIError(500, "store detail: disk full on prod-7")

    monkeypatch.setattr(backend, "add", failing)
    monkeypatch.setattr(backend, "update", failing)
    outcome = await import_payload(backend, TENANT, [{"content": "x"}], on_conflict=on_conflict)
    assert outcome.imported == []
    assert outcome.errors == [{"index": 0, "error": "The memory backend failed (status 500)."}]
    assert "store detail: disk full on prod-7" in caplog.text
    assert "more after the first" not in caplog.text


async def test_import_logs_only_the_first_backend_failure_and_counts_the_rest(
    backend: InMemoryBackend, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    async def failing(*args, **kwargs):
        raise MemoryAPIError(500, "store detail")

    monkeypatch.setattr(backend, "add", failing)
    entries = [{"content": f"m{i}"} for i in range(5)]
    outcome = await import_payload(backend, TENANT, entries, on_conflict="duplicate")
    assert len(outcome.errors) == 5
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert sum("store detail" in m for m in messages) == 1
    assert any("4 more after the first" in m for m in messages)


async def test_import_counts_failures_across_add_and_overwrite(
    backend: InMemoryBackend, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    await backend.add(TENANT, "existing", infer=False)

    async def failing(*args, **kwargs):
        raise MemoryAPIError(500, "store detail")

    monkeypatch.setattr(backend, "add", failing)
    monkeypatch.setattr(backend, "update", failing)
    entries = [{"content": "existing"}, {"content": "new"}]
    outcome = await import_payload(backend, TENANT, entries, on_conflict="overwrite")
    assert [e["index"] for e in outcome.errors] == [0, 1]
    assert "Backend errors on import: 1 more after the first" in caplog.text


async def test_import_reports_per_entry_errors(backend: InMemoryBackend):
    outcome = await import_payload(
        backend, TENANT, [{"content": "ok"}, {"content": ""}, {"nope": 1}]
    )
    assert len(outcome.imported) == 1
    assert [e["index"] for e in outcome.errors] == [1, 2]


async def test_import_non_dict_metadata_is_a_per_entry_error_not_an_exception(
    backend: InMemoryBackend,
):
    """A non-dict `metadata` is an error for that entry, not an exception out of
    import_payload, which the tool handler doesn't catch."""
    outcome = await import_payload(
        backend,
        TENANT,
        [
            {"content": "good one"},
            {"content": "bad", "metadata": "not-a-dict"},
            {"content": "good two"},
        ],
    )
    assert len(outcome.imported) == 2
    assert outcome.errors == [{"index": 1, "error": "metadata must be an object"}]


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


async def test_migrate_reports_an_incomplete_source():
    source, target = PagedBackend(page_size=100, incomplete=True), InMemoryBackend()
    await source.add(TENANT, "fact", infer=False)
    report = await migrate(source, target, TENANT)
    assert report.truncated is True
    assert report.dedup_incomplete is False


async def test_migrate_flags_truncation_across_pages():
    source, target = PagedBackend(page_size=2), InMemoryBackend()
    for i in range(5):
        await source.add(TENANT, f"fact {i}", infer=False)
    report = await migrate(source, target, TENANT, limit=3)
    assert (report.exported, report.truncated) == (3, True)


async def test_migrate_reports_an_incomplete_target_dedup_read():
    source, target = InMemoryBackend(), PagedBackend(page_size=100, incomplete=True)
    await source.add(TENANT, "fact", infer=False)
    report = await migrate(source, target, TENANT)
    assert report.truncated is False
    assert report.dedup_incomplete is True


async def test_migrate_into_a_different_tenant():
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add(TENANT, "carried across", infer=False)
    report = await migrate(source, target, TENANT, target_partition=OTHER)
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


async def test_migrate_copies_system_and_sets_the_target_space():
    source, target = InMemoryBackend(), InMemoryBackend()
    stamp: System = {"space": TENANT, "author": "writer", "created_by": "creator"}
    await source.add(TENANT, "moved fact", system=stamp, infer=False)
    await migrate(source, target, TENANT, target_partition=OTHER)
    landed = await target.list_memories(OTHER)
    assert [m.system for m in landed.memories] == [{**stamp, "space": OTHER}]


class RefusingBackend(InMemoryBackend):
    """Refuses one partition, as a store that can't match it exactly would."""

    def __init__(self, refused: str) -> None:
        super().__init__()
        self.refused = refused

    def validate_partition(self, partition: str) -> None:
        if partition == self.refused:
            raise ValueError(f"this store can't match {partition!r} exactly")


@pytest.mark.parametrize("partition", ["", "*", " * "])
async def test_migrate_refuses_an_empty_or_wildcard_source(partition: str):
    source, target = InMemoryBackend(), InMemoryBackend()
    with pytest.raises(ValueError, match="empty or a wildcard"):
        await migrate(source, target, partition)


@pytest.mark.parametrize("partition", ["", "*"])
async def test_migrate_refuses_an_empty_or_wildcard_target(partition: str):
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add(TENANT, "fact", infer=False)
    with pytest.raises(ValueError, match="empty or a wildcard"):
        await migrate(source, target, TENANT, target_partition=partition)
    assert (await target.list_memories(TENANT)).memories == []


async def test_migrate_refuses_a_source_partition_its_backend_refuses():
    source, target = RefusingBackend(TENANT), InMemoryBackend()
    with pytest.raises(ValueError, match="exactly"):
        await migrate(source, target, TENANT, target_partition=OTHER)


async def test_migrate_refuses_a_target_partition_its_backend_refuses_before_writing():
    source, target = InMemoryBackend(), RefusingBackend(OTHER)
    await source.add(TENANT, "fact", infer=False)
    with pytest.raises(ValueError, match="exactly"):
        await migrate(source, target, TENANT, target_partition=OTHER)


# ---------------------------------------------------------------------------
# Space check on every listed memory
# ---------------------------------------------------------------------------


async def test_export_refuses_a_memory_from_another_partition():
    backend = ForeignPartitionBackend()
    await backend.add(TENANT, "fact", infer=False)
    with pytest.raises(PolicyError) as refused:
        await export_payload(backend, TENANT)
    assert refused.value.code == "internal_error"


async def test_import_dedup_refuses_a_memory_from_another_partition():
    backend = ForeignPartitionBackend()
    await backend.add(TENANT, "fact", infer=False)
    with pytest.raises(PolicyError) as refused:
        await import_payload(backend, TENANT, [{"content": "fact"}])
    assert refused.value.code == "internal_error"


async def test_migrate_refuses_a_source_memory_from_another_partition(
    caplog: pytest.LogCaptureFixture,
):
    source, target = ForeignPartitionBackend(), InMemoryBackend()
    await source.add(TENANT, "fact", infer=False)
    with pytest.raises(PolicyError) as refused:
        await migrate(source, target, TENANT, target_partition=OTHER)
    assert refused.value.code == "internal_error"
    assert "operation=migrate " in caplog.text
    assert (await target.list_memories(OTHER)).memories == []


class ReadBackBackend(InMemoryBackend):
    """Raises a 503 on the `failing`-th `get` call, and answers `update` with a foreign memory."""

    def __init__(self, *, failing: int | None = None, foreign_update: bool = False) -> None:
        super().__init__()
        self.failing = failing
        self.foreign_update = foreign_update
        self.gets = 0

    async def get(self, partition, memory_id):
        self.gets += 1
        if self.gets == self.failing:
            raise MemoryAPIError(503, "down")
        return await super().get(partition, memory_id)

    async def update(self, partition, memory_id, content=None, *, patch=None, system=None):
        updated = await super().update(partition, memory_id, content, patch=patch, system=system)
        if self.foreign_update:
            updated.partition = "elsewhere"
        return updated


async def test_import_records_a_failed_read_back_and_carries_on():
    backend = ReadBackBackend(failing=2)
    entries = [{"content": f"fact {i}"} for i in range(3)]
    outcome = await import_payload(backend, TENANT, entries, on_conflict="duplicate")
    assert [e["index"] for e in outcome.errors] == [1]
    assert [e["index"] for e in outcome.imported] == [0, 2]


async def test_import_refuses_a_created_memory_from_another_partition(
    caplog: pytest.LogCaptureFixture,
):
    class ForeignGet(InMemoryBackend):
        async def get(self, partition, memory_id):
            memory = await super().get(partition, memory_id)
            if memory is not None:
                memory.partition = "elsewhere"
            return memory

    with pytest.raises(PolicyError) as refused:
        await import_payload(ForeignGet(), TENANT, [{"content": "x"}], on_conflict="duplicate")
    assert refused.value.code == "internal_error"
    assert "operation=import " in caplog.text


async def test_import_refuses_an_overwrite_that_returns_another_partition():
    backend = ReadBackBackend(foreign_update=True)
    await backend.add(TENANT, "shared", infer=False)
    with pytest.raises(PolicyError) as refused:
        await import_payload(backend, TENANT, [{"content": "shared"}], on_conflict="overwrite")
    assert refused.value.code == "internal_error"


async def test_a_migration_logs_its_target_side_checks_as_migrate(
    caplog: pytest.LogCaptureFixture,
):
    source, target = InMemoryBackend(), ForeignPartitionBackend()
    await source.add(TENANT, "fact", infer=False)
    await target.add(OTHER, "already there", infer=False)
    with pytest.raises(PolicyError):
        await migrate(source, target, TENANT, target_partition=OTHER)
    assert "operation=migrate " in caplog.text
