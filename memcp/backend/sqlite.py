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
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
    MemoryAPIError,
    paginate,
    reject_nested_filters,
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
    content_after   TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_memory ON history (memory_id);
"""


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
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    # --- internals ---

    async def _run(self, fn: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row, score: float | None = None) -> Memory:
        return Memory(
            id=row["id"],
            content=row["content"],
            score=score,
            scope=json.loads(row["scope"]),
            metadata=json.loads(row["metadata"]),
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
    ) -> list[AddResult]:
        if scope:
            reject_nested_filters(scope)
        memory_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

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
                        json.dumps(metadata or {}),
                        now,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO history (memory_id, action, timestamp, content_before,"
                    " content_after) VALUES (?, 'created', ?, NULL, ?)",
                    (memory_id, now, content),
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
    ) -> Memory:
        now = datetime.now(UTC).isoformat()

        def op() -> Memory:
            row = self._owned_row(user_id, memory_id)
            if row is None:
                raise MemoryAPIError(404, "Not found")
            new_metadata = json.dumps(metadata) if metadata is not None else row["metadata"]
            with self._conn:
                self._conn.execute(
                    "UPDATE memories SET content = ?, updated_at = ?, metadata = ? WHERE id = ?",
                    (content, now, new_metadata, memory_id),
                )
                self._conn.execute(
                    "INSERT INTO history (memory_id, action, timestamp, content_before,"
                    " content_after) VALUES (?, 'updated', ?, ?, ?)",
                    (memory_id, now, row["content"], content),
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
