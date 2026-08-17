"""Cross-backend round trip (A3) asserted against the documented loss set (A4).

Writes the corpus into the source, migrates it, and checks per memory that content
and scope survived and that the query which found it in the source still finds it
in the target. Anything else that changed is compared to
`memcp/conformance/portability.py`: a loss the document does not name fails here.

The target always receives a different tenant id. A migration into a fresh tenant
is the honest shape of the operation, and it keeps a same-backend pair from being
a no-op that import dedup swallows.
"""

from __future__ import annotations

import pytest

from memcp.backend.base import MemoryBackend
from memcp.conformance import portability
from memcp.conformance.corpus import CorpusEntry, round_trip_corpus
from memcp.conformance.registry import BackendSpec
from memcp.conformance.report import Recorder, RoundTrip
from memcp.conformance.suite.conftest import PairSpec
from memcp.migrate import dedup_key, migrate
from memcp.types import Memory

SEARCH_LIMIT = 25
LIST_LIMIT = 200

MIN_MEMORIES = 20
MIN_SCOPES = 2


def test_corpus_meets_the_criterion():
    corpus = round_trip_corpus()
    assert len(corpus) >= MIN_MEMORIES, f"A3 needs at least {MIN_MEMORIES} memories"
    scopes = {tuple(sorted(e.scope.items())) for e in corpus}
    assert len(scopes) >= MIN_SCOPES, f"A3 needs at least {MIN_SCOPES} scopes"
    shared = [e for e in corpus if _content_count(corpus, e.content) > 1]
    assert shared, (
        "the corpus must contain content that appears in more than one scope, or the "
        "round trip stops exercising scope-aware import dedup"
    )


def _content_count(corpus: list[CorpusEntry], content: str) -> int:
    return sum(1 for e in corpus if e.content == content)


async def _write_corpus(backend: MemoryBackend, tenant: str, corpus: list[CorpusEntry]) -> None:
    """Write the corpus, editing one memory on the way in.

    The edit matters: `updated_at` and `history` are declared losses, and a corpus
    of never-touched memories would not produce either, leaving both declarations
    unenforced.
    """
    can_edit = "update_memory" in backend.capabilities()
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


@pytest.mark.conformance("round_trip")
async def test_round_trip_preserves_content_scope_and_retrieval(
    pair: tuple[MemoryBackend, MemoryBackend],
    pair_spec: PairSpec,
    tenant: str,
    other_tenant: str,
    report: Recorder,
):
    source, target = pair
    source_spec, target_spec = pair_spec
    _skip_without_list(source, source_spec)
    _skip_without_list(target, target_spec)

    # Raises UndocumentedPairError if docs/portability.md has no entry for the pair.
    declared = sorted(portability.declared_aspects(source_spec.name, target_spec.name))

    corpus = round_trip_corpus()
    await _write_corpus(source, tenant, corpus)

    for entry in corpus:
        await _assert_retrievable(source, tenant, entry, f"source {source_spec.name}")

    result = await migrate(
        source,
        target,
        tenant,
        target_user_id=other_tenant,
        source_name=source_spec.name,
        target_name=target_spec.name,
        limit=LIST_LIMIT,
    )

    assert not result.errors, f"migration reported errors: {result.errors}"
    assert result.exported == len(corpus), (
        f"exported {result.exported} of {len(corpus)} memories from {source_spec.name}. "
        "A shortfall here is the source collapsing writes, not the migration losing them."
    )
    assert result.skipped == 0, (
        f"{result.skipped} memories were skipped as duplicates importing into "
        f"{target_spec.name}. The corpus holds identical content in two scopes, so a "
        "skip here means dedup is not scope-aware."
    )
    assert result.imported == len(corpus), (
        f"imported {result.imported} of {len(corpus)} memories into {target_spec.name}"
    )

    source_memories = (await source.list_memories(tenant, limit=LIST_LIMIT)).memories
    target_memories = (await target.list_memories(other_tenant, limit=LIST_LIMIT)).memories
    source_index = {dedup_key(m.content, m.scope): m for m in source_memories}
    target_index = {dedup_key(m.content, m.scope): m for m in target_memories}

    compare_history = (
        "memory_history" in source.capabilities() and "memory_history" in target.capabilities()
    )
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
        await _assert_retrievable(target, other_tenant, entry, f"target {target_spec.name}")

    undeclared = sorted(portability.undeclared(source_spec.name, target_spec.name, observed))
    stale = sorted(portability.stale(source_spec.name, target_spec.name, observed))
    report.record_round_trip(
        RoundTrip(
            source=source_spec.name,
            target=target_spec.name,
            exported=result.exported,
            imported=result.imported,
            observed_losses=undeclared,
            declared_losses=declared,
            stale_losses=stale,
        )
    )

    assert not undeclared, (
        f"{source_spec.name} -> {target_spec.name} lost {undeclared}, which "
        f"docs/portability.md does not name. Either the adapter regressed or the loss "
        f"is real and belongs in memcp/conformance/portability.py — decide, do not "
        f"widen the assertion. Documented for this pair: {declared}."
    )
    assert not stale, (
        f"docs/portability.md declares {stale} lost for {source_spec.name} -> "
        f"{target_spec.name}, but the round trip preserved it. An over-claiming "
        "document is as bad as a silent loss: it would let a real regression in "
        "those aspects pass. Remove the declaration or make the corpus produce it."
    )


def _skip_without_list(backend: MemoryBackend, spec: BackendSpec) -> None:
    if "list_memories" not in backend.capabilities():
        pytest.skip(f"{spec.name} does not declare list_memories, so it cannot be migrated")
