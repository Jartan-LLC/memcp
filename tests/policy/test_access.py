"""What a key's relation allows, and which granted spaces startup refuses."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from memcp.backends.capabilities import ALL_CAPABILITIES
from memcp.backends.in_memory import InMemoryBackend
from memcp.identity import reset_key, set_key
from memcp.policy import Policy
from memcp.policy.access import check_granted_spaces
from memcp.types import PolicyError
from tests.keys import make_key

MISSING_ID = "00000000-0000-4000-8000-000000000000"

# architecture.md, "Relation needed per tool", by the policy method behind each tool.
READS = {"search", "get", "list_memories", "history", "entities", "export_memories"}
WRITES = {"add", "update", "delete", "delete_all", "import_memories"}
KEYLESS = {"health", "close"}
BACKEND_DATA_METHODS = {
    "add",
    "search",
    "get",
    "update",
    "delete",
    "delete_all",
    "list_memories",
    "history",
    "entities",
}


class _Untouchable(InMemoryBackend):
    """Declares every capability, and fails a test that reaches any data method."""

    def capabilities(self) -> set[str]:
        return set(ALL_CAPABILITIES)

    def __getattribute__(self, name: str) -> Any:
        if name in BACKEND_DATA_METHODS:
            raise AssertionError(f"policy called the backend's {name}()")
        return super().__getattribute__(name)


# Each write with arguments that would otherwise fail validation.
INVALID_WRITES: dict[str, Callable[[Policy], Coroutine[Any, Any, Any]]] = {
    "add": lambda p: p.add(""),
    "update": lambda p: p.update("not a memory id"),
    "delete": lambda p: p.delete("not a memory id"),
    "delete_all": lambda p: p.delete_all({"user_id": "x"}),
    "import_memories": lambda p: p.import_memories([]),
}

CALLS: dict[str, Callable[[Policy], Coroutine[Any, Any, Any]]] = {
    "search": lambda p: p.search("anything"),
    "get": lambda p: p.get(MISSING_ID),
    "list_memories": lambda p: p.list_memories(),
    "history": lambda p: p.history(MISSING_ID),
    "entities": lambda p: p.entities(),
    "export_memories": lambda p: p.export_memories(),
    "add": lambda p: p.add("a fact", infer=False),
    "update": lambda p: p.update(MISSING_ID, "changed"),
    "delete": lambda p: p.delete(MISSING_ID),
    "delete_all": lambda p: p.delete_all({"agent_id": "a"}),
    "import_memories": lambda p: p.import_memories([{"content": "a fact"}]),
}


def test_every_policy_operation_is_classified():
    """A new operation fails here until it is given a relation."""
    operations = {
        name
        for name, member in inspect.getmembers(Policy, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }
    assert operations == READS | WRITES | KEYLESS
    assert set(CALLS) == READS | WRITES


@pytest.mark.parametrize("operation", sorted(WRITES))
async def test_a_reader_is_refused_every_write_before_its_arguments_are_checked(operation: str):
    policy = Policy(_Untouchable())
    token = set_key(make_key("team", "reader"))
    try:
        with pytest.raises(PolicyError) as raised:
            await INVALID_WRITES[operation](policy)
    finally:
        reset_key(token)
    assert raised.value.code == "forbidden"


@pytest.mark.parametrize("relation", ["reader", "writer"])
@pytest.mark.parametrize("operation", sorted(READS))
async def test_either_relation_may_read(operation: str, relation: str):
    backend = InMemoryBackend()
    backend.capabilities = lambda: set(ALL_CAPABILITIES)  # type: ignore[method-assign]
    policy = Policy(backend)
    token = set_key(make_key("team", relation))  # type: ignore[arg-type]
    try:
        await CALLS[operation](policy)
    except PolicyError as e:
        assert e.code != "forbidden", f"{operation} refused a {relation}"
    except NotImplementedError:
        pass  # in_memory has no entities; the relation check came first and passed
    finally:
        reset_key(token)


@pytest.mark.parametrize("operation", sorted(WRITES))
async def test_a_writer_may_write(operation: str):
    policy = Policy(InMemoryBackend())
    token = set_key(make_key("team", "writer"))
    try:
        await CALLS[operation](policy)
    except PolicyError as e:
        assert e.code != "forbidden", f"{operation} refused a writer"
    finally:
        reset_key(token)


@pytest.mark.parametrize("operation", sorted(READS | WRITES))
async def test_no_operation_runs_without_a_key(operation: str):
    policy = Policy(_Untouchable())
    task = asyncio.create_task(CALLS[operation](policy), context=contextvars.Context())
    with pytest.raises(LookupError):
        await task


async def test_a_refused_write_reaches_no_store_and_changes_nothing():
    backend = InMemoryBackend()
    policy = Policy(backend)
    writer = set_key(make_key("team", "writer"))
    try:
        memory_id = (await policy.add("kept", infer=False))[0].id  # type: ignore[index]
    finally:
        reset_key(writer)
    reader = set_key(make_key("team", "reader", name="ci"))
    try:
        for call in (
            policy.update(memory_id, "changed"),
            policy.delete(memory_id),
            policy.delete_all({"agent_id": "a"}),
        ):
            with pytest.raises(PolicyError, match="forbidden"):
                await call
    finally:
        reset_key(reader)
    stored = await backend.get("team", memory_id)
    assert stored is not None and stored.content == "kept"


# --- granted spaces at startup ---------------------------------------------------


@pytest.mark.parametrize("space", ["*", "", " ", " * "])
def test_an_empty_or_wildcard_space_is_refused(space: str):
    with pytest.raises(ValueError, match="space"):
        check_granted_spaces(InMemoryBackend(), ["team", space])


def test_a_space_the_backend_refuses_is_refused(monkeypatch: pytest.MonkeyPatch):
    backend = InMemoryBackend()

    def refuse(partition: str) -> None:
        if partition == "folded":
            raise ValueError("this store folds case")

    monkeypatch.setattr(backend, "validate_partition", refuse)
    with pytest.raises(ValueError, match=r"'folded'.*this store folds case"):
        check_granted_spaces(backend, ["team", "folded"])


@pytest.mark.parametrize(
    "space",
    ["has space", "tab\tin", "ctrl\x01", "a" * 129, "spc_team", "__health_check__"],
    ids=["space", "tab", "control", "129 characters", "spc_ prefix", "__ prefix"],
)
def test_a_space_that_is_not_portable_is_warned_about_and_served(
    space: str, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.WARNING, logger="memcp.policy.access"):
        check_granted_spaces(InMemoryBackend(), [space])
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert repr(space) in caplog.records[0].getMessage()


@pytest.mark.parametrize("space", ["team", "a" * 128, "Team.Notes-2", "équipe"])
def test_an_ordinary_space_passes_silently(space: str, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="memcp.policy.access"):
        check_granted_spaces(InMemoryBackend(), [space])
    assert caplog.records == []
