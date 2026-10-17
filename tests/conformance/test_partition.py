"""Partition conformance: a backend refuses what its store can't match exactly, and keeps
every partition it accepts apart from the ones a store might read it as.

Required for every backend, so nothing here skips on capabilities; optional methods are
called only when declared. A pair test skips when the backend refuses one of its
partitions.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable

import pytest

from memcp.backends.base import MemoryBackend
from memcp.types import AddResult, MemoryAPIError

MISSING_ID = "00000000-0000-4000-8000-000000000000"


def _first_id(result: AddResult | list[AddResult]) -> str:
    items = result if isinstance(result, list) else [result]
    assert items, "add() stored nothing"
    return items[0].id


def _accepts(backend: MemoryBackend, partition: str) -> bool:
    try:
        backend.validate_partition(partition)
    except ValueError:
        return False
    return True


def _data_calls(
    backend: MemoryBackend, partition: str
) -> dict[str, Callable[[], Awaitable[object]]]:
    """One call per data method the backend implements, each naming `partition`."""
    scope = {backend.scope_keys()[0]: "conf-partition"}
    calls: dict[str, Callable[[], Awaitable[object]]] = {
        "add": lambda: backend.add(partition, "conformance refused partition", infer=False),
        "search": lambda: backend.search(partition, "refused partition"),
        "get": lambda: backend.get(partition, MISSING_ID),
        "delete": lambda: backend.delete(partition, MISSING_ID),
        "delete_all": lambda: backend.delete_all(partition, scope),
    }
    declared = backend.capabilities()
    if "update" in declared:
        calls["update"] = lambda: backend.update(partition, MISSING_ID, "replacement")
    if "list" in declared:
        calls["list"] = lambda: backend.list_memories(partition)
    if "history" in declared:
        calls["history"] = lambda: backend.history(partition, MISSING_ID)
    if "entities" in declared:
        calls["entities"] = lambda: backend.entities(partition)
    return calls


async def test_validate_partition_accepts_an_ordinary_partition(
    backend: MemoryBackend, tenant: str
):
    assert backend.validate_partition(tenant) is None


async def test_every_data_method_refuses_a_refused_partition(
    backend: MemoryBackend, tenant: str, monkeypatch: pytest.MonkeyPatch
):
    refused = f"{tenant}_refused"

    def refuse(partition: str) -> None:
        if partition == refused:
            raise ValueError("refused for this test")

    monkeypatch.setattr(backend, "validate_partition", refuse)
    for name, call in _data_calls(backend, refused).items():
        with pytest.raises(MemoryAPIError) as raised:
            await call()
        assert raised.value.status == 400, f"{name}() answered {raised.value.status}, not 400"

    monkeypatch.undo()
    stored = await backend.search(refused, "conformance refused partition", limit=10)
    assert not stored, "add() stored a memory under a partition it refused"


# Partitions a store might read as the test's partition, or as every partition.
NEAR_VARIANTS: dict[str, Callable[[str], str]] = {
    "leading space": lambda base: f" {base}",
    "trailing space": lambda base: f"{base} ",
    "trailing tab": lambda base: f"{base}\t",
    "upper case": str.upper,
    "one more character": lambda base: f"{base}x",
    "one less character": lambda base: base[:-1],
    "wildcard": lambda base: "*",
    "empty": lambda base: "",
}


async def _assert_apart(backend: MemoryBackend, stored: str, probe: str) -> None:
    """A memory written under `stored` can't be read, found or deleted from `probe`."""
    scope_key = backend.scope_keys()[0]
    scope = {scope_key: "conf-near-variant"}
    content = "conformance near variant partition marker okapi"
    memory_id = _first_id(await backend.add(stored, content, scope=scope, infer=False))

    assert await backend.get(probe, memory_id) is None, "get() crossed partitions"
    found = await backend.search(probe, "near variant partition okapi", limit=10)
    assert not any(m.id == memory_id for m in found), "search() crossed partitions"
    if "list" in backend.capabilities():
        page = await backend.list_memories(probe)
        assert not any(m.id == memory_id for m in page.memories), "list crossed partitions"
    with pytest.raises(MemoryAPIError) as missing:
        await backend.delete(probe, memory_id)
    assert missing.value.status == 404
    await backend.delete_all(probe, scope)
    assert await backend.get(stored, memory_id) is not None, "delete_all() crossed partitions"


@pytest.mark.parametrize("variant", list(NEAR_VARIANTS))
async def test_an_accepted_partition_is_apart_from_its_near_variant(
    backend: MemoryBackend, tenant: str, variant: str
):
    probe = NEAR_VARIANTS[variant](tenant)
    if not _accepts(backend, probe):
        pytest.skip("refused by validate_partition, so nothing reaches the store under it")
    await _assert_apart(backend, tenant, probe)
    if probe and probe != "*":
        try:
            await _assert_apart(backend, probe, tenant)
        finally:
            await backend.delete_all(probe, {backend.scope_keys()[0]: "conf-near-variant"})


async def test_unicode_normalization_forms_are_apart(backend: MemoryBackend, tenant: str):
    composed = unicodedata.normalize("NFC", f"{tenant}é")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    if not (_accepts(backend, composed) and _accepts(backend, decomposed)):
        pytest.skip("refused by validate_partition, so nothing reaches the store under it")
    try:
        await _assert_apart(backend, composed, decomposed)
    finally:
        await backend.delete_all(composed, {backend.scope_keys()[0]: "conf-near-variant"})
