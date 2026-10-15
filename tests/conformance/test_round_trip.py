"""Cross-backend round trip, asserted against the losses a migration is allowed.

Writes the corpus into the source, migrates it, and checks per memory that content
and scope survived and that the query which found it in the source still finds it
in the target. Anything else that changed must be one of LOSSES, and every one of
LOSSES must actually happen: an over-claimed loss would let a real regression pass.

The target always receives a different tenant id, so a same-backend pair is a real
migration rather than a no-op that import dedup swallows.
"""

from __future__ import annotations

import pytest

from memcp.backends.base import MemoryBackend
from memcp.policy.transfer import dedup_key, migrate
from memcp.types import Memory
from tests.conformance.corpus import CorpusEntry, round_trip_corpus

# Smaller than every scope in the corpus, so a search that ignores the query fails.
SEARCH_LIMIT = 3
LIST_LIMIT = 200

# Import calls add() on the target, which mints a new id and timestamps, and export
# carries current content only, not the change log. docs/reference.md lists these, plus
# `author`, which the corpus doesn't set (tests/test_attribution.py covers it).
LOSSES = frozenset({"memory_id", "created_at", "updated_at", "history"})


async def _write_corpus(backend: MemoryBackend, tenant: str, corpus: list[CorpusEntry]) -> None:
    """Write the corpus, editing one memory on the way in.

    The edit matters: `updated_at` and `history` are in LOSSES, and a corpus of
    never-touched memories would lose neither, so every pair would fail.
    """
    can_edit = "update" in backend.capabilities()
    for i, entry in enumerate(corpus):
        content = f"{entry.content} (draft)" if i == 0 and can_edit else entry.content
        result = await backend.add(
            tenant,
            content,
            scope=dict(entry.scope),
            metadata=dict(entry.metadata),
            infer=False,
        )
        if i == 0 and can_edit:
            items = result if isinstance(result, list) else [result]
            assert items, "add() stored nothing for the corpus entry that gets edited"
            await backend.update(tenant, items[0].id, entry.content, metadata=dict(entry.metadata))


async def _assert_retrievable(
    backend: MemoryBackend, tenant: str, entry: CorpusEntry, where: str
) -> None:
    hits = await backend.search(tenant, entry.query, scope=dict(entry.scope), limit=SEARCH_LIMIT)
    assert any(m.content == entry.content for m in hits), (
        f"{where}: query {entry.query!r} in scope {entry.scope} did not return "
        f"{entry.content!r}; got {[m.content for m in hits]}"
    )


def _observed_losses(
    corpus: list[CorpusEntry],
    source: dict[tuple[str, tuple[tuple[str, str], ...]], Memory],
    target: dict[tuple[str, tuple[tuple[str, str], ...]], Memory],
    *,
    compare_history: bool,
    history_lengths: dict[str, tuple[int, int]],
) -> set[str]:
    observed: set[str] = set()
    target_contents = {m.content for m in target.values()}
    for entry in corpus:
        key = dedup_key(entry.content, entry.scope)
        src = source.get(key)
        assert src is not None, (
            f"the source lost {entry.content!r} in scope {entry.scope} before migration ran"
        )
        dst = target.get(key)
        if dst is None:
            observed.add("content" if entry.content not in target_contents else "scope")
            continue
        if dst.metadata != src.metadata:
            observed.add("metadata")
        if dst.id != src.id:
            observed.add("memory_id")
        if dst.created_at != src.created_at:
            observed.add("created_at")
        if dst.updated_at != src.updated_at:
            observed.add("updated_at")
    if compare_history:
        for src_len, dst_len in history_lengths.values():
            if dst_len < src_len:
                observed.add("history")
    return observed


async def test_round_trip_preserves_content_scope_and_retrieval(
    pair: tuple[MemoryBackend, MemoryBackend],
    pair_names: tuple[str, str],
    tenant: str,
    other_tenant: str,
):
    source, target = pair
    source_name, target_name = pair_names
    for backend, name in ((source, source_name), (target, target_name)):
        if "list" not in backend.capabilities():
            pytest.skip(f"{name} does not declare list_memories, so it cannot be migrated")

    corpus = round_trip_corpus()
    await _write_corpus(source, tenant, corpus)

    for entry in corpus:
        await _assert_retrievable(source, tenant, entry, f"source {source_name}")

    result = await migrate(
        source,
        target,
        tenant,
        target_user_id=other_tenant,
        source_name=source_name,
        target_name=target_name,
        limit=LIST_LIMIT,
    )

    assert not result.errors, f"migration reported errors: {result.errors}"
    assert result.exported == len(corpus), (
        f"exported {result.exported} of {len(corpus)} memories from {source_name}. "
        "A shortfall here is the source collapsing writes, not the migration losing them."
    )
    assert result.skipped == 0, (
        f"{result.skipped} memories were skipped as duplicates importing into "
        f"{target_name}. The corpus holds identical content in two scopes, so a "
        "skip here means dedup is not scope-aware."
    )
    assert result.imported == len(corpus), (
        f"imported {result.imported} of {len(corpus)} memories into {target_name}"
    )

    source_memories = (await source.list_memories(tenant, limit=LIST_LIMIT)).memories
    target_memories = (await target.list_memories(other_tenant, limit=LIST_LIMIT)).memories
    source_index = {dedup_key(m.content, m.scope): m for m in source_memories}
    target_index = {dedup_key(m.content, m.scope): m for m in target_memories}

    compare_history = "history" in source.capabilities() and "history" in target.capabilities()
    history_lengths: dict[str, tuple[int, int]] = {}
    if compare_history:
        for key, src in source_index.items():
            dst = target_index.get(key)
            if dst is None:
                continue
            src_len = len(await source.history(tenant, src.id))
            dst_len = len(await target.history(other_tenant, dst.id))
            history_lengths[src.id] = (src_len, dst_len)

    observed = _observed_losses(
        corpus,
        source_index,
        target_index,
        compare_history=compare_history,
        history_lengths=history_lengths,
    )

    for entry in corpus:
        await _assert_retrievable(target, other_tenant, entry, f"target {target_name}")

    # History is only comparable when both sides report it.
    expected = LOSSES if compare_history else LOSSES - {"history"}
    assert observed == expected, (
        f"{source_name} -> {target_name} lost {sorted(observed)}; expected exactly "
        f"{sorted(expected)}. An extra loss is a regression; a missing one means LOSSES "
        "and docs/reference.md over-claim."
    )
