"""Live mem0 backend integration tests.

Skipped unless MEM0_API_BASE and MEM0_API_KEY are set.
Uses a dedicated test user to avoid polluting real data.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import AsyncGenerator

import pytest

from memcp.backend.mem0 import Mem0Backend

MEM0_API_BASE = os.environ.get("MEM0_API_BASE")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY")

pytestmark = pytest.mark.skipif(
    not MEM0_API_BASE or not MEM0_API_KEY,
    reason="MEM0_API_BASE and MEM0_API_KEY not set",
)

TEST_USER = f"memcp_test_{uuid.uuid4().hex[:8]}"
SECOND_USER = f"memcp_test2_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def mem0() -> AsyncGenerator[Mem0Backend]:
    assert MEM0_API_BASE and MEM0_API_KEY
    backend = Mem0Backend(MEM0_API_BASE, MEM0_API_KEY)
    yield backend
    # Cleanup: delete all test user memories for both users
    for uid in (TEST_USER, SECOND_USER):
        with contextlib.suppress(Exception):
            listing = await backend.list_memories(uid)
            for m in listing.memories:
                await backend.delete(uid, m.id)
    await backend.close()


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


async def test_add_and_search(mem0: Mem0Backend):
    results = await mem0.add(TEST_USER, "memcp integration test fact", infer=False)
    assert len(results) >= 1
    memory_id = results[0].id
    assert memory_id

    search_results = await mem0.search(TEST_USER, "integration test")
    assert len(search_results) >= 1
    assert any(m.id == memory_id for m in search_results)

    # Cleanup
    await mem0.delete(TEST_USER, memory_id)


async def test_get_memory(mem0: Mem0Backend):
    results = await mem0.add(TEST_USER, "get test memory", infer=False)
    memory_id = results[0].id

    memory = await mem0.get(TEST_USER, memory_id)
    assert memory is not None
    assert memory.content == "get test memory"
    assert memory.id == memory_id

    await mem0.delete(TEST_USER, memory_id)


async def test_get_nonexistent(mem0: Mem0Backend):
    result = await mem0.get(TEST_USER, "00000000-0000-0000-0000-000000000000")
    assert result is None


async def test_update_memory(mem0: Mem0Backend):
    results = await mem0.add(TEST_USER, "original content", infer=False)
    memory_id = results[0].id

    updated = await mem0.update(TEST_USER, memory_id, "updated content")
    assert updated.content == "updated content"

    fetched = await mem0.get(TEST_USER, memory_id)
    assert fetched is not None
    assert fetched.content == "updated content"

    await mem0.delete(TEST_USER, memory_id)


async def test_delete_memory(mem0: Mem0Backend):
    results = await mem0.add(TEST_USER, "to be deleted", infer=False)
    memory_id = results[0].id

    deleted = await mem0.delete(TEST_USER, memory_id)
    assert deleted is True

    fetched = await mem0.get(TEST_USER, memory_id)
    assert fetched is None


async def test_list_memories(mem0: Mem0Backend):
    await mem0.add(TEST_USER, "list test one", infer=False)
    await mem0.add(TEST_USER, "list test two", infer=False)

    listing = await mem0.list_memories(TEST_USER)
    assert len(listing.memories) >= 2

    # Cleanup
    for m in listing.memories:
        await mem0.delete(TEST_USER, m.id)


async def test_history(mem0: Mem0Backend):
    results = await mem0.add(TEST_USER, "history test", infer=False)
    memory_id = results[0].id

    entries = await mem0.history(TEST_USER, memory_id)
    assert len(entries) >= 1
    assert entries[0].action in ("add", "created")

    await mem0.delete(TEST_USER, memory_id)


async def test_health(mem0: Mem0Backend):
    status = await mem0.health()
    assert status.status == "healthy"
    assert status.backend == "mem0"
    assert status.latency_ms is not None


async def test_capabilities(mem0: Mem0Backend):
    caps = mem0.capabilities()
    assert "get_memory" in caps
    assert "update_memory" in caps
    assert "list_memories" in caps


async def test_scope_keys(mem0: Mem0Backend):
    keys = mem0.scope_keys()
    assert "agent_id" in keys
    assert "run_id" in keys


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_entities_tenant_isolation(mem0: Mem0Backend):
    """entities() should only return the requesting user's data."""
    await mem0.add(TEST_USER, "user A entity test", infer=False)
    await mem0.add(SECOND_USER, "user B entity test", infer=False)

    result_a = await mem0.entities(TEST_USER)
    result_b = await mem0.entities(SECOND_USER)

    assert len(result_a.entities) >= 1, "User A should have at least one entity"
    assert len(result_b.entities) >= 1, "User B should have at least one entity"

    a_ids = {e.get("id") for e in result_a.entities}
    b_ids = {e.get("id") for e in result_b.entities}

    assert SECOND_USER not in a_ids, "User A sees User B's entities"
    assert TEST_USER not in b_ids, "User B sees User A's entities"


# ---------------------------------------------------------------------------
# Infer behavior
# ---------------------------------------------------------------------------


async def test_add_with_infer(mem0: Mem0Backend):
    results = await mem0.add(
        TEST_USER,
        "My name is TestUser and I prefer Python 3.12 for all projects",
        infer=True,
    )
    # infer=true may extract facts or return empty
    if results:
        for r in results:
            await mem0.delete(TEST_USER, r.id)


async def test_add_without_infer(mem0: Mem0Backend):
    results = await mem0.add(TEST_USER, "verbatim storage test", infer=False)
    assert len(results) == 1
    memory = await mem0.get(TEST_USER, results[0].id)
    assert memory is not None
    assert memory.content == "verbatim storage test"
    await mem0.delete(TEST_USER, results[0].id)
