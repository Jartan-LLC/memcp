"""Each decision `Policy` makes, tested directly with input that breaks it."""

from __future__ import annotations

from typing import Any

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.identity import DEFAULT_PRINCIPAL, Principal, reset_tenant, set_principal, set_tenant
from memcp.policy import Policy
from memcp.types import (
    AUTHOR_METADATA_KEY,
    MAX_IMPORT,
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


async def test_scope_accepts_a_number(policy: Policy):
    await policy.search("q", scope={"run_id": 7})


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
    inside = Memory(id="in", content="in", scope={"agent_id": "a", "run_id": "7"})
    outside = Memory(id="out", content="out", scope={"agent_id": "b"})
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
    _returning(backend, "search", [Memory(id="unscoped", content="x", scope={})])
    assert await policy.search("q", scope={"run_id": "None"}) == []


async def test_a_boolean_scope_matches_its_wire_form(policy: Policy, backend: InMemoryBackend):
    stored = Memory(id="flagged", content="x", scope={"run_id": "true"})
    _returning(backend, "search", [stored])
    assert [m.id for m in await policy.search("q", scope={"run_id": True})] == ["flagged"]


@pytest.mark.parametrize("scope", ["agent-a", "", [], 0, False])
async def test_import_reports_an_entry_whose_scope_is_not_an_object(policy: Policy, scope: Any):
    outcome = await policy.import_memories([{"content": "x", "scope": scope}])
    assert outcome.imported == []
    assert outcome.errors[0]["index"] == 0


async def test_a_number_matches_an_equal_number_of_another_type(
    policy: Policy, backend: InMemoryBackend
):
    _returning(backend, "search", [Memory(id="seven", content="x", scope={"run_id": 7})])
    assert [m.id for m in await policy.search("q", scope={"run_id": 7.0})] == ["seven"]


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
    await policy.add("x", metadata={AUTHOR_METADATA_KEY: "forged", "k": "v"}, infer=False)
    assert passed == [{"k": "v"}]


async def test_update_strips_reserved_metadata(policy: Policy, backend: InMemoryBackend):
    memory_id = await _add_one(policy)
    passed = _record_metadata(backend, "update")
    await policy.update(memory_id, "x", metadata={AUTHOR_METADATA_KEY: "forged", "k": "v"})
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
    monkeypatch.setattr(backend, "capabilities", lambda: {"list_memories"})
    policy = Policy(backend)
    await _refused(
        policy.import_memories([{"content": "x"}], on_conflict="overwrite"), "not_supported"
    )


async def test_import_reports_an_entry_with_a_bad_scope(policy: Policy):
    outcome = await policy.import_memories([{"content": "x", "scope": {"colour": "blue"}}])
    assert outcome.imported == []
    assert outcome.errors[0]["index"] == 0
    assert "Unknown scope keys" in outcome.errors[0]["error"]
