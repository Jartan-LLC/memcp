"""Each decision `Policy` makes, tested directly with input that breaks it."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.identity import DEFAULT_PRINCIPAL, Principal, reset_tenant, set_principal, set_tenant
from memcp.policy import Policy
from memcp.types import (
    MAX_IMPORT,
    SYSTEM_METADATA_KEY,
    AddResult,
    ListResult,
    Memory,
    MemoryAPIError,
    PolicyError,
)


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


@pytest.fixture
def policy(backend: InMemoryBackend) -> Policy:
    return Policy(backend)


def test_a_backend_without_a_retrieval_kind_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delattr(InMemoryBackend, "retrieval")
    with pytest.raises(TypeError, match="retrieval"):
        Policy(InMemoryBackend())


def test_a_backend_with_an_unknown_retrieval_kind_is_refused(backend: InMemoryBackend):
    backend.retrieval = "vector"  # type: ignore[assignment]
    with pytest.raises(TypeError, match="'vector'"):
        Policy(backend)


def test_a_backend_with_an_unknown_capability_is_refused(
    backend: InMemoryBackend, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(backend, "capabilities", lambda: {"update", "list_memories"})
    with pytest.raises(TypeError, match="'list_memories'"):
        Policy(backend)


async def _refused(call: Any, code: str) -> str:
    with pytest.raises(PolicyError) as raised:
        await call
    assert raised.value.code == code
    return raised.value.message


async def _add_one(policy: Policy, content: str = "a fact") -> str:
    result = await policy.add(content, infer=False)
    return (result[0] if isinstance(result, list) else result).id


async def test_add_refuses_empty_content(policy: Policy):
    await _refused(policy.add("", infer=False), "validation_error")


async def test_search_refuses_a_threshold_outside_0_to_1(policy: Policy):
    message = await _refused(policy.search("q", threshold=1.5), "validation_error")
    assert "threshold" in message


async def test_search_refuses_an_empty_query(policy: Policy):
    await _refused(policy.search(""), "validation_error")


async def test_list_refuses_a_limit_out_of_range(policy: Policy):
    await _refused(policy.list_memories(limit=0), "validation_error")


async def test_list_refuses_an_unreadable_cursor(policy: Policy):
    await _refused(policy.list_memories(cursor="not-a-number"), "validation_error")


async def test_entities_refuses_a_limit_out_of_range(policy: Policy):
    await _refused(policy.entities(limit=0), "validation_error")


BY_ID: dict[str, Any] = {
    "get": lambda p, memory_id: p.get(memory_id),
    "update": lambda p, memory_id: p.update(memory_id, "x"),
    "delete": lambda p, memory_id: p.delete(memory_id),
    "history": lambda p, memory_id: p.history(memory_id),
}


@pytest.mark.parametrize("operation", BY_ID)
async def test_a_by_id_call_refuses_a_malformed_id(policy: Policy, operation: str):
    await _refused(BY_ID[operation](policy, "not a valid id!"), "validation_error")


async def test_scope_refuses_an_unknown_key(policy: Policy):
    await _refused(policy.add("x", scope={"colour": "blue"}, infer=False), "invalid_scope")


async def test_scope_refuses_a_nested_filter(policy: Policy):
    await _refused(policy.search("q", scope={"AND": [{"agent_id": "a"}]}), "nested_filter")


async def test_scope_refuses_an_agent_id_with_nothing_left_after_normalizing(policy: Policy):
    await _refused(policy.add("x", scope={"agent_id": "!!!"}, infer=False), "validation_error")


async def test_scope_normalizes_agent_id(policy: Policy, backend: InMemoryBackend):
    await policy.add("scoped", scope={"agent_id": " Claude Code "}, infer=False)
    listed = await backend.list_memories("test_user", scope={"agent_id": "claude-code"})
    assert [m.content for m in listed.memories] == ["scoped"]


SCOPED: dict[str, Any] = {
    "add": lambda p, scope: p.add("x", scope=scope, infer=False),
    "search": lambda p, scope: p.search("q", scope=scope),
    "list_memories": lambda p, scope: p.list_memories(scope=scope),
    "delete_all": lambda p, scope: p.delete_all(scope),
    "entities": lambda p, scope: p.entities(scope=scope),
}


@pytest.mark.parametrize("operation", SCOPED)
async def test_scope_refuses_user_id(policy: Policy, backend: InMemoryBackend, operation: str):
    message = await _refused(
        SCOPED[operation](policy, {"user_id": "someone-else", "agent_id": "a"}), "invalid_scope"
    )
    assert "user_id" in message and "token" in message
    assert (await backend.list_memories("someone-else")).memories == []


@pytest.mark.parametrize("operation", SCOPED)
@pytest.mark.parametrize("value", ["*", "", "  ", None])
async def test_scope_refuses_a_value_that_matches_anything(
    policy: Policy, operation: str, value: Any
):
    await _refused(SCOPED[operation](policy, {"run_id": value}), "validation_error")


@pytest.mark.parametrize("value", [" s1", "s1 ", "\ts1"])
async def test_scope_refuses_whitespace_at_either_end(
    backend: InMemoryBackend, monkeypatch: pytest.MonkeyPatch, value: str
):
    monkeypatch.setattr(backend, "scope_keys", lambda: ["agent_id", "run_id", "session"])
    policy = Policy(backend)
    await _refused(policy.search("q", scope={"session": value}), "validation_error")
    await policy.search("q", scope={"session": "s 1"})


async def test_scope_refuses_whitespace_inside_a_run_id(policy: Policy):
    await _refused(policy.search("q", scope={"run_id": "run one"}), "validation_error")


@pytest.mark.parametrize("value", [7, 0, 0.0, False])
async def test_scope_accepts_a_number_or_boolean(policy: Policy, value: Any):
    await policy.search("q", scope={"run_id": value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_scope_refuses_a_number_that_is_not_finite(policy: Policy, value: float):
    await _refused(policy.search("q", scope={"run_id": value}), "validation_error")


def _returning(backend: InMemoryBackend, method: str, memories: list[Memory]) -> None:
    """Make `method` return `memories` whatever was asked, like a backend that ignores scope."""

    async def ignoring_scope(*args: Any, **kwargs: Any) -> Any:
        if method == "list_memories":
            return ListResult(memories=memories)
        return memories

    setattr(backend, method, ignoring_scope)


@pytest.mark.parametrize("method", ["search", "list_memories"])
async def test_a_returned_memory_outside_the_scope_is_dropped(
    policy: Policy,
    backend: InMemoryBackend,
    method: str,
    caplog: pytest.LogCaptureFixture,
):
    inside = Memory(
        id="in", content="in", partition="test_user", scope={"agent_id": "a", "run_id": "7"}
    )
    outside = Memory(id="out", content="out", partition="test_user", scope={"agent_id": "b"})
    _returning(backend, method, [inside, outside])
    if method == "search":
        found = await policy.search("q", scope={"agent_id": "a", "run_id": 7})
    else:
        found = (await policy.list_memories(scope={"agent_id": "a", "run_id": 7})).memories
    assert [m.id for m in found] == ["in"]
    assert "Dropped memory out " in caplog.text


async def test_a_memory_without_the_key_is_outside_its_scope(
    policy: Policy, backend: InMemoryBackend
):
    _returning(
        backend, "search", [Memory(id="unscoped", content="x", partition="test_user", scope={})]
    )
    # "None" is what str() makes of a missing value; it must not match.
    assert await policy.search("q", scope={"run_id": "None"}) == []


async def test_a_boolean_scope_matches_its_wire_form(policy: Policy, backend: InMemoryBackend):
    stored = Memory(id="flagged", content="x", partition="test_user", scope={"run_id": "true"})
    _returning(backend, "search", [stored])
    assert [m.id for m in await policy.search("q", scope={"run_id": True})] == ["flagged"]


@pytest.mark.parametrize("scope", ["agent-a", "", [], 0, False])
async def test_import_reports_an_entry_whose_scope_is_not_an_object(policy: Policy, scope: Any):
    outcome = await policy.import_memories([{"content": "x", "scope": scope}])
    assert outcome.imported == []
    assert outcome.errors[0]["index"] == 0


@pytest.mark.parametrize(("stored", "requested"), [(7, 7.0), (1, True)])
async def test_a_value_equal_only_as_python_is_outside_its_scope(
    policy: Policy, backend: InMemoryBackend, stored: object, requested: object
):
    _returning(
        backend,
        "search",
        [Memory(id="m", content="x", partition="test_user", scope={"run_id": stored})],
    )
    assert await policy.search("q", scope={"run_id": requested}) == []


async def test_delete_all_requires_a_scope(policy: Policy):
    await _refused(policy.delete_all({}), "scope_required")


@pytest.mark.parametrize("value", ["*", "", None])
async def test_delete_all_refuses_a_wildcard_value(policy: Policy, value: Any):
    await _refused(policy.delete_all({"agent_id": value}), "validation_error")


async def test_delete_all_refuses_a_wildcard_next_to_a_concrete_value(policy: Policy):
    await _refused(policy.delete_all({"agent_id": "a", "run_id": "*"}), "validation_error")


async def test_get_of_a_missing_memory_is_not_found(policy: Policy):
    await _refused(policy.get("missing-id"), "not_found")


@pytest.mark.parametrize("operation", BY_ID)
async def test_a_by_id_call_on_another_tenants_memory_is_not_found(policy: Policy, operation: str):
    memory_id = await _add_one(policy)
    token = set_tenant("someone_else")
    try:
        await _refused(BY_ID[operation](policy, memory_id), "not_found")
    finally:
        reset_tenant(token)


async def test_update_of_a_missing_memory_is_not_found(policy: Policy):
    await _refused(policy.update("missing-id", "x"), "not_found")


async def test_delete_of_a_missing_memory_is_not_found(policy: Policy):
    await _refused(policy.delete("missing-id"), "not_found")


async def test_history_of_a_missing_memory_is_not_found(policy: Policy):
    await _refused(policy.history("missing-id"), "not_found")


async def test_a_backend_error_other_than_404_is_not_a_refusal(
    policy: Policy, backend: InMemoryBackend, monkeypatch: pytest.MonkeyPatch
):
    async def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise MemoryAPIError(503, "down")

    monkeypatch.setattr(backend, "get", unavailable)
    with pytest.raises(MemoryAPIError):
        await policy.get("some-id")


def _record_metadata(backend: InMemoryBackend, method: str) -> list[Any]:
    """Record the metadata policy passes to `method`; the backend's own strip can't hide it."""
    seen: list[Any] = []
    original = getattr(backend, method)

    async def recording(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("metadata"))
        return await original(*args, **kwargs)

    setattr(backend, method, recording)
    return seen


async def test_add_strips_reserved_metadata(policy: Policy, backend: InMemoryBackend):
    passed = _record_metadata(backend, "add")
    await policy.add("x", metadata={SYSTEM_METADATA_KEY: "forged", "k": "v"}, infer=False)
    assert passed == [{"k": "v"}]


async def test_update_strips_reserved_metadata(policy: Policy, backend: InMemoryBackend):
    memory_id = await _add_one(policy)
    passed = _record_metadata(backend, "update")
    await policy.update(memory_id, "x", metadata={SYSTEM_METADATA_KEY: "forged", "k": "v"})
    assert passed == [{"k": "v"}]


async def test_a_write_by_a_resolved_key_is_stamped_with_its_seat(
    policy: Policy, backend: InMemoryBackend
):
    token = set_principal(Principal(tenant="test_user", seat="laptop"))
    try:
        memory_id = await _add_one(policy)
    finally:
        reset_tenant(token)
    stored = await backend.get("test_user", memory_id)
    assert stored is not None
    assert stored.author == "laptop"


async def test_a_dev_mode_write_has_no_author(policy: Policy, backend: InMemoryBackend):
    token = set_principal(DEFAULT_PRINCIPAL)
    try:
        memory_id = await _add_one(policy)
        stored = await backend.get(DEFAULT_PRINCIPAL.tenant, memory_id)
    finally:
        reset_tenant(token)
    assert stored is not None
    assert stored.author is None


async def test_import_refuses_an_empty_batch(policy: Policy):
    await _refused(policy.import_memories([]), "validation_error")


async def test_import_refuses_a_batch_over_the_limit(policy: Policy):
    too_many = [{"content": f"m{i}"} for i in range(MAX_IMPORT + 1)]
    await _refused(policy.import_memories(too_many), "validation_error")


async def test_import_refuses_an_unknown_conflict_mode(policy: Policy):
    await _refused(
        policy.import_memories([{"content": "x"}], on_conflict="merge"), "validation_error"
    )


async def test_import_overwrite_needs_the_update_capability(
    backend: InMemoryBackend, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(backend, "capabilities", lambda: {"list"})
    policy = Policy(backend)
    await _refused(
        policy.import_memories([{"content": "x"}], on_conflict="overwrite"), "not_supported"
    )


async def test_import_reports_an_entry_with_a_bad_scope(policy: Policy):
    outcome = await policy.import_memories([{"content": "x", "scope": {"colour": "blue"}}])
    assert outcome.imported == []
    assert outcome.errors[0]["index"] == 0
    assert "Unknown scope keys" in outcome.errors[0]["error"]


# ---------------------------------------------------------------------------
# space check
# ---------------------------------------------------------------------------

LEAKED = "content from another space"
TENANT = "test_user"


def _returned(**changes: Any) -> Memory:
    """A memory as a backend returns it: in the caller's space unless `changes` move it."""
    fields: dict[str, Any] = {
        "id": "foreign",
        "content": LEAKED,
        "partition": TENANT,
        "system": {"space": TENANT},
    }
    fields.update(changes)
    return Memory(**fields)


FOREIGN = [
    pytest.param({"partition": "someone_else"}, id="partition"),
    pytest.param({"system": {"space": "someone_else"}}, id="system-space"),
]


def _get_returns(backend: InMemoryBackend, memory: Memory) -> None:
    async def get(*args: Any, **kwargs: Any) -> Memory:
        return memory

    backend.get = get  # type: ignore[method-assign]


async def _isolation_refused(call: Any, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="memcp.policy"):
        await _refused(call, "internal_error")
    assert any("isolation_violation" in r.getMessage() for r in caplog.records)
    assert LEAKED not in caplog.text


READS: dict[str, Any] = {
    "search": ("search", lambda p: p.search("q")),
    "list": ("list_memories", lambda p: p.list_memories()),
    "export": ("list_memories", lambda p: p.export_memories()),
}


@pytest.mark.parametrize("changes", FOREIGN)
@pytest.mark.parametrize("read", READS)
async def test_a_read_returning_another_space_fails_closed(
    policy: Policy,
    backend: InMemoryBackend,
    caplog: pytest.LogCaptureFixture,
    read: str,
    changes: dict[str, Any],
):
    method, call = READS[read]
    _returning(backend, method, [_returned(id="first"), _returned(**changes)])
    await _isolation_refused(call(policy), caplog)


@pytest.mark.parametrize("changes", FOREIGN)
@pytest.mark.parametrize("operation", BY_ID)
async def test_a_by_id_call_reaching_another_space_fails_closed(
    policy: Policy,
    backend: InMemoryBackend,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    changes: dict[str, Any],
):
    memory_id = await _add_one(policy)
    _get_returns(backend, _returned(id=memory_id, **changes))
    await _isolation_refused(BY_ID[operation](policy, memory_id), caplog)
    assert f"operation={operation} " in caplog.text
    assert memory_id in backend._store, "the refused call still changed the memory"  # pyright: ignore[reportPrivateUsage]
    assert backend._store[memory_id]["content"] == "a fact"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("changes", FOREIGN)
async def test_an_add_whose_memory_lands_in_another_space_fails_closed(
    policy: Policy,
    backend: InMemoryBackend,
    caplog: pytest.LogCaptureFixture,
    changes: dict[str, Any],
):
    """Two items, the second foreign, so a check of only the first would pass."""

    async def add(*args: Any, **kwargs: Any) -> list[AddResult]:
        return [AddResult(id="first"), AddResult(id="second")]

    async def get(partition: str, memory_id: str) -> Memory:
        return _returned(id=memory_id, **(changes if memory_id == "second" else {}))

    backend.add = add  # type: ignore[method-assign]
    backend.get = get  # type: ignore[method-assign]
    await _isolation_refused(policy.add("x", infer=False), caplog)


async def test_an_add_whose_memory_cannot_be_read_back_fails_closed(
    policy: Policy, backend: InMemoryBackend, caplog: pytest.LogCaptureFixture
):
    async def get(*args: Any, **kwargs: Any) -> None:
        return None

    backend.get = get  # type: ignore[method-assign]
    await _isolation_refused(policy.add("x", infer=False), caplog)


@pytest.mark.parametrize("changes", FOREIGN)
async def test_an_update_that_returns_another_space_fails_closed(
    policy: Policy,
    backend: InMemoryBackend,
    caplog: pytest.LogCaptureFixture,
    changes: dict[str, Any],
):
    memory_id = await _add_one(policy)

    async def update(*args: Any, **kwargs: Any) -> Memory:
        return _returned(id=memory_id, **changes)

    backend.update = update  # type: ignore[method-assign]
    await _isolation_refused(policy.update(memory_id, "x"), caplog)


async def test_a_memory_without_system_fields_passes_on_its_partition(
    policy: Policy, backend: InMemoryBackend
):
    _get_returns(backend, _returned(system={}))
    assert (await policy.get("foreign")).id == "foreign"


# ---------------------------------------------------------------------------
# system stamps
# ---------------------------------------------------------------------------


def _as(seat: str) -> Any:
    return set_principal(Principal(tenant=TENANT, seat=seat))


async def _stored_system(backend: InMemoryBackend, memory_id: str) -> Any:
    stored = await backend.get(TENANT, memory_id)
    assert stored is not None
    return stored.system


async def test_add_stamps_the_caller_as_author_and_creator(
    policy: Policy, backend: InMemoryBackend
):
    token = _as("laptop")
    try:
        memory_id = await _add_one(policy)
    finally:
        reset_tenant(token)
    assert await _stored_system(backend, memory_id) == {
        "space": TENANT,
        "author": "laptop",
        "created_by": "laptop",
    }


async def test_update_keeps_the_creator_and_stamps_the_caller(
    policy: Policy, backend: InMemoryBackend
):
    token = _as("laptop")
    try:
        memory_id = await _add_one(policy)
    finally:
        reset_tenant(token)
    token = _as("phone")
    try:
        await policy.update(memory_id, "changed")
    finally:
        reset_tenant(token)
    assert await _stored_system(backend, memory_id) == {
        "space": TENANT,
        "author": "phone",
        "created_by": "laptop",
    }


async def test_update_of_a_memory_without_a_creator_adds_none(
    policy: Policy, backend: InMemoryBackend
):
    memory_id = (await backend.add(TENANT, "older memory", infer=False))[0].id
    token = _as("phone")
    try:
        await policy.update(memory_id, "changed")
    finally:
        reset_tenant(token)
    assert await _stored_system(backend, memory_id) == {"space": TENANT, "author": "phone"}


async def test_a_dev_mode_write_stamps_only_the_space(policy: Policy, backend: InMemoryBackend):
    token = set_principal(DEFAULT_PRINCIPAL)
    try:
        memory_id = await _add_one(policy)
        stored = await backend.get(DEFAULT_PRINCIPAL.tenant, memory_id)
    finally:
        reset_tenant(token)
    assert stored is not None
    assert stored.system == {"space": DEFAULT_PRINCIPAL.tenant}


async def test_import_stamps_the_importer_whatever_the_entry_says(
    policy: Policy, backend: InMemoryBackend
):
    token = _as("importer")
    try:
        outcome = await policy.import_memories(
            [{"content": "imported", "author": "forged", "system": {"created_by": "forged"}}]
        )
    finally:
        reset_tenant(token)
    assert await _stored_system(backend, outcome.imported[0]["id"]) == {
        "space": TENANT,
        "author": "importer",
        "created_by": "importer",
    }


async def test_an_import_overwrite_keeps_the_creator(policy: Policy, backend: InMemoryBackend):
    token = _as("laptop")
    try:
        memory_id = await _add_one(policy, "shared fact")
    finally:
        reset_tenant(token)
    token = _as("importer")
    try:
        await policy.import_memories([{"content": "shared fact"}], on_conflict="overwrite")
    finally:
        reset_tenant(token)
    assert await _stored_system(backend, memory_id) == {
        "space": TENANT,
        "author": "importer",
        "created_by": "laptop",
    }
