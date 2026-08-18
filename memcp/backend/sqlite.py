"""SQLite backend — durable local storage with no API key and no signup.

This is the keyless half of memcp's position: `memcp up` reaches a backend that
survives a restart without anyone holding an LLM provider account. It stores the
same shapes `in_memory` does, in a file, so the two are interchangeable and the
round trip between them loses nothing but identity.

What it is not: a semantic engine. Retrieval is word overlap against stored
content, the same scoring `in_memory` uses, so results are lexical and
deterministic rather than embedding-ranked. Fact extraction does not happen
either — `add(infer=True)` stores the text verbatim, because extraction is an LLM
behaviour and an LLM is what this backend exists to avoid needing. Both are
documented in the README rather than approximated.

Concurrency: one connection in WAL mode behind a lock, with every call run off
the event loop via `asyncio.to_thread`. `sqlite3` is stdlib, so this backend adds
no dependency.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memcp.types import (
    AUTHOR_METADATA_KEY,
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
    MemoryAPIError,
    paginate,
    reject_nested_filters,
    split_author,
    strip_reserved_metadata,
)

from .base import MemoryBackend
from .keyword import score as _score

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT '{}',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories (user_id);

CREATE TABLE IF NOT EXISTS history (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       TEXT NOT NULL,
    action          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    content_before  TEXT,
    content_after   TEXT,
    author          TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_memory ON history (memory_id);
"""

# `PRAGMA user_version` bump gating the one-time reserved-metadata cleanse below —
# see `_cleanse_preexisting_reserved_metadata`. Bump this if a future reserved key
# needs the same one-time treatment.
_RESERVED_METADATA_CLEANSE_VERSION = 1


class SqliteBackend(MemoryBackend):
    """Durable, keyless memory storage in a single SQLite file."""

    def __init__(self, path: str | Path = "memcp.sqlite3") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL keeps a reader from blocking the writer; both are this process, but a
        # crash mid-write is the case that matters for a file people rely on.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate_history_author_column()
        self._cleanse_preexisting_reserved_metadata()
        self._conn.commit()

    def _migrate_history_author_column(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` does not add columns to a file that
        already has the table — a sqlite file created before this column existed
        needs it added explicitly, once."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(history)")}
        if "author" not in columns:
            self._conn.execute("ALTER TABLE history ADD COLUMN author TEXT")

    def _cleanse_preexisting_reserved_metadata(self) -> None:
        """Strip any reserved-namespace metadata key from every row already in
        this file, once per file (marked via `PRAGMA user_version`, the same
        kind of one-time-migration need `_migrate_history_author_column` has,
        via a different mechanism since this is a data change, not a schema
        one).

        `add`/`update` strip the reserved namespace from caller-supplied
        metadata unconditionally, so nothing written through this class from
        here on can carry a forged key — but that guard did not always exist.
        Before it did, `metadata` was entirely unvalidated (correction B), so
        any bearer-token holder could have stored a `_memcp_author` key
        directly, and the read path cannot otherwise tell that from a real
        server stamp (Thorne, JAR-723 finding 1). A row in an existing file
        predates every version of the write-time guard by definition, so
        stripping it once, unconditionally, is exact — nothing legitimate is
        lost, because nothing in this namespace has ever been a caller's to
        set."""
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= _RESERVED_METADATA_CLEANSE_VERSION:
            return
        rows = self._conn.execute("SELECT id, metadata FROM memories").fetchall()
        for row in rows:
            stored = json.loads(row["metadata"])
            cleaned = strip_reserved_metadata(stored)
            if cleaned != stored:
                self._conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(cleaned), row["id"]),
                )
        self._conn.execute(f"PRAGMA user_version = {_RESERVED_METADATA_CLEANSE_VERSION}")

    @property
    def path(self) -> Path:
        return self._path

    # --- internals ---

    async def _run(self, fn: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row, score: float | None = None) -> Memory:
        author, metadata = split_author(json.loads(row["metadata"]))
        return Memory(
            id=row["id"],
            content=row["content"],
            score=score,
            scope=json.loads(row["scope"]),
            metadata=metadata,
            author=author,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _owned_row(self, user_id: str, memory_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)
        )
        return cur.fetchone()

    # --- required ---

    async def add(
        self,
        user_id: str,
        content: str,
        *,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
        author: str | None = None,
    ) -> list[AddResult]:
        if scope:
            reject_nested_filters(scope)
        memory_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        # Stripped here, not just by the tool layer — see in_memory.add()'s
        # comment on the same line; a caller cannot plant the reserved key
        # regardless of which caller reaches this method.
        stored_metadata = strip_reserved_metadata(metadata) or {}
        if author is not None:
            stored_metadata[AUTHOR_METADATA_KEY] = author

        def op() -> None:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO memories (id, user_id, content, scope, metadata, created_at,"
                    " updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (
                        memory_id,
                        user_id,
                        content,
                        json.dumps(scope or {}),
                        json.dumps(stored_metadata),
                        now,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO history (memory_id, action, timestamp, content_before,"
                    " content_after, author) VALUES (?, 'created', ?, NULL, ?, ?)",
                    (memory_id, now, content, author),
                )

        await self._run(op)
        return [AddResult(id=memory_id, status="ready", created_at=now)]

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]:
        if scope:
            reject_nested_filters(scope)

        def op() -> list[Memory]:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE user_id = ?", (user_id,)
            ).fetchall()
            scored: list[tuple[float, sqlite3.Row]] = []
            for row in rows:
                if scope and not _scope_matches(json.loads(row["scope"]), scope):
                    continue
                score = _score(query, row["content"])
                if score is None:
                    continue
                scored.append((score, row))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [self._row_to_memory(row, score) for score, row in scored[:limit]]

        return await self._run(op)

    async def delete(self, user_id: str, memory_id: str) -> bool:
        def op() -> bool:
            if self._owned_row(user_id, memory_id) is None:
                raise MemoryAPIError(404, "Not found")
            with self._conn:
                self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                self._conn.execute("DELETE FROM history WHERE memory_id = ?", (memory_id,))
            return True

        return await self._run(op)

    async def delete_all(self, user_id: str, scope: dict[str, Any]) -> int:
        reject_nested_filters(scope)

        def op() -> int:
            rows = self._conn.execute(
                "SELECT id, scope FROM memories WHERE user_id = ?", (user_id,)
            ).fetchall()
            doomed = [row["id"] for row in rows if _scope_matches(json.loads(row["scope"]), scope)]
            if not doomed:
                return 0
            marks = ",".join("?" * len(doomed))
            with self._conn:
                self._conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", doomed)
                self._conn.execute(f"DELETE FROM history WHERE memory_id IN ({marks})", doomed)
            return len(doomed)

        return await self._run(op)

    async def health(self) -> HealthStatus:
        started = time.perf_counter()

        def op() -> None:
            self._conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone()

        try:
            await self._run(op)
        except sqlite3.Error:
            return HealthStatus(status="unhealthy", backend="sqlite", latency_ms=None)
        return HealthStatus(
            status="healthy",
            backend="sqlite",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    def capabilities(self) -> set[str]:
        # No memory_entities: see entities() below.
        return {
            "get_memory",
            "update_memory",
            "list_memories",
            "memory_history",
        }

    def scope_keys(self) -> list[str]:
        return ["agent_id", "run_id"]

    # --- optional ---

    async def get(self, user_id: str, memory_id: str) -> Memory | None:
        def op() -> Memory | None:
            row = self._owned_row(user_id, memory_id)
            return None if row is None else self._row_to_memory(row)

        return await self._run(op)

    async def update(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        author: str | None = None,
    ) -> Memory:
        now = datetime.now(UTC).isoformat()

        def op() -> Memory:
            row = self._owned_row(user_id, memory_id)
            if row is None:
                raise MemoryAPIError(404, "Not found")
            if metadata is not None or author is not None:
                raw = metadata if metadata is not None else json.loads(row["metadata"])
                base = strip_reserved_metadata(raw) or {}
                if author is not None:
                    base[AUTHOR_METADATA_KEY] = author
                new_metadata = json.dumps(base)
            else:
                new_metadata = row["metadata"]
            with self._conn:
                self._conn.execute(
                    "UPDATE memories SET content = ?, updated_at = ?, metadata = ? WHERE id = ?",
                    (content, now, new_metadata, memory_id),
                )
                self._conn.execute(
                    "INSERT INTO history (memory_id, action, timestamp, content_before,"
                    " content_after, author) VALUES (?, 'updated', ?, ?, ?, ?)",
                    (memory_id, now, row["content"], content, author),
                )
            updated = self._owned_row(user_id, memory_id)
            if updated is None:  # pragma: no cover - written in the same transaction
                raise MemoryAPIError(404, "Not found")
            return self._row_to_memory(updated)

        return await self._run(op)

    async def list_memories(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        def op() -> list[Memory]:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE user_id = ? ORDER BY rowid", (user_id,)
            ).fetchall()
            return [
                self._row_to_memory(row)
                for row in rows
                if not scope or _scope_matches(json.loads(row["scope"]), scope)
            ]

        memories = await self._run(op)
        return paginate(memories, cursor, limit)

    async def history(self, user_id: str, memory_id: str) -> list[HistoryEntry]:
        def op() -> list[HistoryEntry]:
            if self._owned_row(user_id, memory_id) is None:
                raise MemoryAPIError(404, "Not found")
            rows = self._conn.execute(
                "SELECT * FROM history WHERE memory_id = ? ORDER BY seq", (memory_id,)
            ).fetchall()
            return [
                HistoryEntry(
                    action=row["action"],
                    timestamp=row["timestamp"],
                    content_before=row["content_before"],
                    content_after=row["content_after"],
                    author=row["author"],
                )
                for row in rows
            ]

        return await self._run(op)

    async def entities(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> EntitiesResult:
        """Not implemented: there is no graph here to return.

        This backend used to answer with one synthetic node carrying a memory count
        and no relationships. That made `memory_status` advertise the knowledge-graph
        capability on the default install while the documentation said the graph
        needs a key — and an agent reads `memory_status`, not the README. Declaring
        nothing is the honest answer; `memory_entities` is simply not registered.
        """
        raise NotImplementedError

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)


def _scope_matches(stored: dict[str, Any], wanted: dict[str, Any]) -> bool:
    return all(stored.get(k) == v for k, v in wanted.items())
