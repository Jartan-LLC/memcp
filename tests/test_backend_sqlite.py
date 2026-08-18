"""The one thing the conformance suite cannot check about the sqlite backend.

Conformance builds a backend, exercises it and throws it away, so it proves the
contract holds but says nothing about whether anything survives the process. That is
the entire reason this backend exists, so it gets its own test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memcp.backend.sqlite import SqliteBackend
from memcp.types import MemoryAPIError


async def test_memories_survive_a_restart(tmp_path: Path):
    path = tmp_path / "brain.sqlite3"

    first = SqliteBackend(path)
    added = await first.add("alice", "the office wifi password is on the fridge")
    memory_id = added[0].id
    await first.close()

    # A different process would look exactly like this: a fresh object over the file.
    second = SqliteBackend(path)
    found = await second.search("alice", "wifi password")
    assert [m.content for m in found] == ["the office wifi password is on the fridge"]

    fetched = await second.get("alice", memory_id)
    assert fetched is not None
    assert fetched.id == memory_id
    await second.close()


async def test_history_survives_a_restart(tmp_path: Path):
    path = tmp_path / "brain.sqlite3"
    first = SqliteBackend(path)
    added = await first.add("alice", "original")
    memory_id = added[0].id
    await first.update("alice", memory_id, "revised")
    await first.close()

    second = SqliteBackend(path)
    entries = await second.history("alice", memory_id)
    assert [e.action for e in entries] == ["created", "updated"]
    assert entries[1].content_before == "original"
    await second.close()


async def test_the_file_is_created_with_its_parent_directory(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "nested" / "deeper" / "brain.sqlite3")
    await backend.add("alice", "anything")
    assert (tmp_path / "nested" / "deeper" / "brain.sqlite3").exists()
    await backend.close()


async def test_tenants_are_separated_in_storage(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "brain.sqlite3")
    added = await backend.add("alice", "alice's note")
    await backend.add("mallory", "mallory's note")

    assert await backend.get("mallory", added[0].id) is None
    with pytest.raises(MemoryAPIError):
        await backend.delete("mallory", added[0].id)
    assert len((await backend.list_memories("alice")).memories) == 1
    await backend.close()


async def test_delete_all_by_scope_leaves_other_scopes(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "brain.sqlite3")
    await backend.add("alice", "one", scope={"agent_id": "a"})
    await backend.add("alice", "two", scope={"agent_id": "b"})

    assert await backend.delete_all("alice", {"agent_id": "a"}) == 1
    remaining = (await backend.list_memories("alice")).memories
    assert [m.content for m in remaining] == ["two"]
    await backend.close()


async def test_health_reports_the_backend_name(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "brain.sqlite3")
    status = await backend.health()
    assert (status.status, status.backend) == ("healthy", "sqlite")
    await backend.close()


async def test_search_ranks_the_way_in_memory_ranks(tmp_path: Path):
    """The two backends share a scoring rule, which is what makes the pair portable."""
    from memcp.backend.in_memory import InMemoryBackend

    corpus = ["python and ruff", "python only", "unrelated entirely"]
    sqlite_backend = SqliteBackend(tmp_path / "brain.sqlite3")
    memory_backend = InMemoryBackend()
    for content in corpus:
        await sqlite_backend.add("alice", content)
        await memory_backend.add("alice", content)

    a = [m.content for m in await sqlite_backend.search("alice", "python ruff")]
    b = [m.content for m in await memory_backend.search("alice", "python ruff")]
    assert a == b
    await sqlite_backend.close()
