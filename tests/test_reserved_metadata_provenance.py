"""A caller-supplied reserved metadata key must never be served as server-resolved
provenance — Thorne's JAR-723 pre-merge gate finding 1.

`metadata` was entirely unvalidated before this fix's write-time guard existed
(correction B), so any bearer-token holder could have stored `_memcp_author`
directly, on any backend. The tool-layer strip (`tests/test_attribution.py`) only
guards a caller reaching `add_memory`/`update_memory`/`import_memories` through the
MCP surface; these tests are about the backend layer itself, and about data that
predates any version of the write-time guard.

Two closures, both Thorne named as sufficient for the backends they cover:
- `in_memory`/`sqlite`: `add`/`update` strip the reserved namespace from
  caller-supplied metadata unconditionally now, regardless of caller — so nothing
  written through a patched build's backend layer can carry a forged key, even
  bypassing the tool layer. sqlite additionally runs a one-time, marked cleanse on
  every row already in a file when a patched build first opens it, closing the gap
  for genuinely pre-existing data.
- `mem0`: has no migration hook (memcp cannot enumerate every tenant a mem0
  install holds), so this backend's closure is operational — a documented
  precondition and a startup warning, not a code guarantee. See `docs/reference.md`
  and the module docstring in `memcp/backend/mem0.py`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import respx

from memcp.backend.in_memory import InMemoryBackend
from memcp.backend.mem0 import Mem0Backend
from memcp.backend.sqlite import SCHEMA, SqliteBackend
from memcp.types import AUTHOR_METADATA_KEY, serialize_memory

# ---------------------------------------------------------------------------
# in_memory / sqlite — the write path itself refuses a forged key, regardless
# of caller. This is the pre-patch call shape: metadata passed straight
# through, no `author` kwarg — exactly how a caller reached these methods
# before this fix's guard existed at all.
# ---------------------------------------------------------------------------


async def test_in_memory_add_refuses_a_caller_supplied_reserved_key():
    backend = InMemoryBackend()
    await backend.add("t", "planted", metadata={AUTHOR_METADATA_KEY: "forged", "note": "kept"})
    memory_id = next(iter(backend._store))
    memory = await backend.get("t", memory_id)

    assert memory is not None
    assert memory.author is None
    assert serialize_memory(memory)["attributed"] is False
    assert memory.metadata == {"note": "kept"}


async def test_in_memory_update_refuses_a_caller_supplied_reserved_key():
    backend = InMemoryBackend()
    added = await backend.add("t", "original", author="trusted")
    memory_id = added[0].id

    await backend.update(
        "t", memory_id, "edited", metadata={AUTHOR_METADATA_KEY: "forged", "note": "kept"}
    )
    memory = await backend.get("t", memory_id)

    assert memory is not None
    assert memory.author is None
    assert memory.metadata == {"note": "kept"}


async def test_sqlite_add_refuses_a_caller_supplied_reserved_key(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "gate.sqlite3")
    await backend.add("t", "planted", metadata={AUTHOR_METADATA_KEY: "forged", "note": "kept"})
    memory = (await backend.list_memories("t")).memories[0]

    assert memory.author is None
    assert memory.metadata == {"note": "kept"}
    await backend.close()


async def test_sqlite_update_refuses_a_caller_supplied_reserved_key(tmp_path: Path):
    backend = SqliteBackend(tmp_path / "gate.sqlite3")
    added = await backend.add("t", "original", author="trusted")
    memory_id = added[0].id

    await backend.update(
        "t", memory_id, "edited", metadata={AUTHOR_METADATA_KEY: "forged", "note": "kept"}
    )
    memory = await backend.get("t", memory_id)

    assert memory is not None
    assert memory.author is None
    assert memory.metadata == {"note": "kept"}
    await backend.close()


# ---------------------------------------------------------------------------
# sqlite — the one-time cleanse. A row inserted directly into the file, the
# way a genuinely pre-patch memcp version (or a bearer-token holder, since
# metadata was unvalidated) would have written it, with no `author` kwarg
# concept and no strip at all. A patched build must not trust it on first
# open, and must not need to be told to look for it.
# ---------------------------------------------------------------------------


def _seed_legacy_row(path: Path, *, memory_id: str, metadata: dict) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO memories (id, user_id, content, scope, metadata, created_at, updated_at)"
        " VALUES (?, 't', 'pre-existing content', '{}', ?, '2026-01-01T00:00:00Z', NULL)",
        (memory_id, json.dumps(metadata)),
    )
    conn.commit()
    conn.close()


async def test_sqlite_cleanses_a_genuinely_pre_existing_forged_row_on_first_open(
    tmp_path: Path,
):
    path = tmp_path / "legacy.sqlite3"
    _seed_legacy_row(
        path, memory_id="legacy-1", metadata={AUTHOR_METADATA_KEY: "forged", "note": "kept"}
    )

    backend = SqliteBackend(path)  # first open by a patched build
    memory = await backend.get("t", "legacy-1")

    assert memory is not None
    assert memory.author is None
    assert memory.metadata == {"note": "kept"}

    # The cleanse rewrote the file itself, not just the in-memory read — a
    # second open (or a raw read) must not find the forged key either.
    raw = (
        sqlite3.connect(path)
        .execute("SELECT metadata FROM memories WHERE id = 'legacy-1'")
        .fetchone()[0]
    )
    assert AUTHOR_METADATA_KEY not in json.loads(raw)
    await backend.close()


async def test_sqlite_cleanse_runs_exactly_once_per_file(tmp_path: Path):
    """PRAGMA user_version marks the cleanse as done — reopening the same file
    must not re-scan every row on every startup."""
    path = tmp_path / "legacy.sqlite3"
    _seed_legacy_row(path, memory_id="legacy-1", metadata={"note": "kept"})

    first = SqliteBackend(path)
    version_after_first_open = first._conn.execute("PRAGMA user_version").fetchone()[0]
    await first.close()

    assert version_after_first_open >= 1

    second = SqliteBackend(path)
    version_after_second_open = second._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version_after_second_open == version_after_first_open
    await second.close()


async def test_sqlite_cleanse_does_not_touch_ordinary_metadata(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    _seed_legacy_row(path, memory_id="legacy-1", metadata={"tag": "ordinary", "count": 3})

    backend = SqliteBackend(path)
    memory = await backend.get("t", "legacy-1")

    assert memory is not None
    assert memory.metadata == {"tag": "ordinary", "count": 3}
    await backend.close()


# ---------------------------------------------------------------------------
# mem0 — documented, not code-closed. Recorded so the gap is visible in the
# test suite rather than only in prose; not a security guarantee to defend.
# ---------------------------------------------------------------------------


async def test_mem0_has_no_migration_hook_for_genuinely_pre_existing_data():
    """mem0 has no local file memcp can scan and rewrite, so a row that
    reached mem0's store before this server version — by any client, since
    `metadata` was unvalidated pre-patch — is not cleansed by this code. The
    precondition is operational: run a metadata sweep on the store before a
    patched build serves it (docs/reference.md). This test documents the
    current, known shape of that gap rather than asserting it is safe."""
    base = "https://mem0.test"
    backend = Mem0Backend(base, "key")
    upstream_row = {
        "id": "legacy-1",
        "memory": "pre-existing content",
        "metadata": {AUTHOR_METADATA_KEY: "forged", "note": "kept"},
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
    wire = serialize_memory(memory)
    assert wire["author"] == "forged"
    assert wire["attributed"] is True
    await backend.close()
