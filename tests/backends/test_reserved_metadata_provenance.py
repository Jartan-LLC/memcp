"""A caller-supplied reserved metadata key must never be served as server-resolved
provenance.

`metadata` was unvalidated before the write-time guard existed, so any bearer-token
holder could have stored a reserved key such as `_memcp_system` directly, on any
backend. The policy-layer refusal (`memcp/policy/metadata.py`, driven through the MCP
surface in `tests/test_attribution.py`) only guards a caller reaching
`add_memory`/`update_memory`/`import_memories`; these tests
are about the backend layer itself, and about data that predates any version of the
write-time guard.

Two closures, each sufficient for the backends it covers:
- `in_memory`/`sqlite`: `add`/`update` refuse a reserved key in caller-supplied
  metadata, regardless of caller, and system fields live outside metadata — so a key
  planted in metadata can never read back as a server-set field.
- `mem0`: has no migration hook (memcp cannot enumerate every tenant a mem0
  install holds), so this backend's closure is operational — a documented
  precondition and a startup warning, not a code guarantee. See `docs/reference.md`
  and the startup warning in `Mem0Backend.__init__` (`memcp/backends/mem0/adapter.py`).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from memcp.backends.base import MetadataPatch
from memcp.backends.in_memory import InMemoryBackend
from memcp.backends.mem0 import Mem0Backend
from memcp.backends.sqlite import SqliteBackend
from memcp.types import SYSTEM_METADATA_KEY, MemoryAPIError, serialize_memory

# ---------------------------------------------------------------------------
# in_memory / sqlite — the write path refuses a reserved key whoever calls it:
# metadata passed straight through, with no `system` argument.
# ---------------------------------------------------------------------------

FORGED = {SYSTEM_METADATA_KEY: {"author": "forged"}, "note": "kept"}


def _backends(tmp_path: Path) -> list[InMemoryBackend | SqliteBackend]:
    return [InMemoryBackend(), SqliteBackend(tmp_path / "gate.sqlite3")]


async def test_add_refuses_a_caller_supplied_reserved_key(tmp_path: Path):
    for backend in _backends(tmp_path):
        with pytest.raises(MemoryAPIError) as refused:
            await backend.add("t", "planted", metadata=FORGED)
        assert refused.value.status == 400
        assert (await backend.list_memories("t")).memories == []
        await backend.close()


async def test_update_refuses_a_caller_supplied_reserved_key(tmp_path: Path):
    for backend in _backends(tmp_path):
        added = await backend.add("t", "original", system={"author": "trusted"})
        with pytest.raises(MemoryAPIError) as refused:
            await backend.update("t", added[0].id, "edited", patch=MetadataPatch(set=FORGED))
        assert refused.value.status == 400
        memory = await backend.get("t", added[0].id)
        assert memory is not None
        assert (memory.content, memory.author, memory.metadata) == ("original", "trusted", {})
        await backend.close()


# ---------------------------------------------------------------------------
# mem0 — documented, not code-closed. Recorded so the gap is visible in the
# test suite rather than only in prose; not a security guarantee to defend.
# ---------------------------------------------------------------------------


async def test_mem0_has_no_migration_hook_for_genuinely_pre_existing_data():
    """mem0 keeps system fields in metadata, and memcp can't enumerate a mem0
    install's tenants to scan them, so a `_memcp_system` key stored by 0.1.2 or
    earlier, which kept callers' metadata as given, reads back as server-set. The
    precondition is operational: sweep the store for that key (docs/reference.md).
    This test documents the current, known shape of that gap rather than asserting it
    is safe."""
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
