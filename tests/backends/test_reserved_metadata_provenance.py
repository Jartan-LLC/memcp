"""A caller-supplied reserved metadata key must never be served as server-resolved
provenance.

`metadata` was unvalidated before the write-time guard existed, so any bearer-token
holder could have stored a reserved key such as `_memcp_system` directly, on any
backend. The tool-layer
strip (`tests/test_attribution.py`) only guards a caller reaching
`add_memory`/`update_memory`/`import_memories` through the MCP surface; these tests
are about the backend layer itself, and about data that predates any version of the
write-time guard.

Two closures, each sufficient for the backends it covers:
- `in_memory`/`sqlite`: `add`/`update` strip the reserved namespace from
  caller-supplied metadata unconditionally, regardless of caller, and system fields
  live outside metadata — so a key planted in metadata can never read back as a
  server-set field.
- `mem0`: has no migration hook (memcp cannot enumerate every tenant a mem0
  install holds), so this backend's closure is operational — a documented
  precondition and a startup warning, not a code guarantee. See `docs/reference.md`
  and the startup warning in `Mem0Backend.__init__` (`memcp/backends/mem0/adapter.py`).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from memcp.backends.in_memory import InMemoryBackend
from memcp.backends.mem0 import Mem0Backend
from memcp.backends.sqlite import SqliteBackend
from memcp.types import SYSTEM_METADATA_KEY, serialize_memory

# ---------------------------------------------------------------------------
# in_memory / sqlite — the write path itself refuses a forged key, regardless
# of caller. This is the pre-patch call shape: metadata passed straight
# through, no `system` kwarg — exactly how a caller reached these methods
# before this fix's guard existed at all.
# ---------------------------------------------------------------------------


async def test_in_memory_add_refuses_a_caller_supplied_reserved_key():
    backend = InMemoryBackend()
    await backend.add("t", "planted", metadata={SYSTEM_METADATA_KEY: "forged", "note": "kept"})
    memory_id = next(iter(backend._store))
    memory = await backend.get("t", memory_id)

    assert memory is not None
    assert memory.author is None
    assert serialize_memory(memory)["author"] is None
    assert memory.metadata == {"note": "kept"}


async def test_in_memory_update_refuses_a_caller_supplied_reserved_key():
    backend = InMemoryBackend()
    added = await backend.add("t", "original", system={"author": "trusted"})
    memory_id = added[0].id

    await backend.update(
        "t", memory_id, "edited", metadata={SYSTEM_METADATA_KEY: "forged", "note": "kept"}
    )
    memory = await backend.get("t", memory_id)

    assert memory is not None
    assert memory.author == "trusted"
    assert memory.metadata == {"note": "kept"}


async def test_sqlite_add_refuses_a_caller_supplied_reserved_key(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "gate.sqlite3")
    await backend.add("t", "planted", metadata={SYSTEM_METADATA_KEY: "forged", "note": "kept"})
    memory = (await backend.list_memories("t")).memories[0]

    assert memory.author is None
    assert memory.metadata == {"note": "kept"}
    await backend.close()


async def test_sqlite_update_refuses_a_caller_supplied_reserved_key(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "gate.sqlite3")
    added = await backend.add("t", "original", system={"author": "trusted"})
    memory_id = added[0].id

    await backend.update(
        "t", memory_id, "edited", metadata={SYSTEM_METADATA_KEY: "forged", "note": "kept"}
    )
    memory = await backend.get("t", memory_id)

    assert memory is not None
    assert memory.author == "trusted"
    assert memory.metadata == {"note": "kept"}
    await backend.close()


# ---------------------------------------------------------------------------
# mem0 — documented, not code-closed. Recorded so the gap is visible in the
# test suite rather than only in prose; not a security guarantee to defend.
# ---------------------------------------------------------------------------


async def test_mem0_has_no_migration_hook_for_genuinely_pre_existing_data():
    """mem0 keeps system fields in metadata, and memcp can't enumerate a mem0
    install's tenants to scan them, so a `_memcp_system` key that reached the store
    before this server version — by any client, since `metadata` was unvalidated
    pre-patch — reads back as server-set. The precondition is operational: sweep the
    store for that key before a patched build serves it (docs/reference.md). This
    test documents the current, known shape of that gap rather than asserting it is
    safe."""
    base = "https://mem0.test"
    backend = Mem0Backend(base, "key")
    upstream_row = {
        "id": "legacy-1",
        "memory": "pre-existing content",
        "metadata": {SYSTEM_METADATA_KEY: {"author": "forged"}, "note": "kept"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": None,
        "user_id": "t",
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/memories/legacy-1").mock(
            return_value=httpx.Response(200, json=upstream_row)
        )
        memory = await backend.get("t", "legacy-1")

    assert memory is not None
    assert serialize_memory(memory)["author"] == "forged"
    await backend.close()
