"""In-memory backend adapter — for conformance tests, dev mode, and demos.

Stores memories in plain dicts. No persistence, no extraction, no vector search.
Search is the keyword scorer in `memcp.backend.keyword`, shared with the sqlite
backend so the two rank identically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
)

from .base import MemoryBackend
from .keyword import score as _score


def _entry_to_memory(mid: str, entry: dict[str, Any], *, score: float | None = None) -> Memory:
    author, metadata = split_author(entry.get("metadata", {}))
    return Memory(
        id=mid,
        content=entry["content"],
        score=score,
        scope=entry.get("scope", {}),
        metadata=metadata,
        author=author,
        created_at=entry["created_at"],
        updated_at=entry.get("updated_at"),
    )


class InMemoryBackend(MemoryBackend):
    """Trivial in-memory implementation of the MemoryBackend protocol."""

    def __init__(self) -> None:
        # {memory_id: {user_id, content, scope, metadata, created_at, updated_at}}
        self._store: dict[str, dict[str, Any]] = {}
        # {memory_id: [HistoryEntry, ...]}
        self._history: dict[str, list[dict[str, Any]]] = {}

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
        stored_metadata = dict(metadata or {})
        if author is not None:
            stored_metadata[AUTHOR_METADATA_KEY] = author
        self._store[memory_id] = {
            "user_id": user_id,
            "content": content,
            "scope": scope or {},
            "metadata": stored_metadata,
            "created_at": now,
            "updated_at": None,
        }
        self._history[memory_id] = [
            {
                "action": "created",
                "timestamp": now,
                "content_before": None,
                "content_after": content,
                "author": author,
            }
        ]
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
        results = []
        for mid, entry in self._store.items():
            if entry["user_id"] != user_id:
                continue
            if scope:
                entry_scope = entry.get("scope", {})
                if not all(entry_scope.get(k) == v for k, v in scope.items()):
                    continue
            relevance = _score(query, entry["content"])
            if relevance is None:
                continue
            results.append((relevance, mid, entry))
        results.sort(key=lambda x: x[0], reverse=True)
        return [_entry_to_memory(mid, entry, score=score) for score, mid, entry in results[:limit]]

    async def delete(self, user_id: str, memory_id: str) -> bool:
        entry = self._store.get(memory_id)
        if entry is None:
            raise MemoryAPIError(404, "Not found")
        if entry["user_id"] != user_id:
            raise MemoryAPIError(404, "Not found")
        del self._store[memory_id]
        self._history.pop(memory_id, None)
        return True

    async def delete_all(self, user_id: str, scope: dict[str, Any]) -> int:
        reject_nested_filters(scope)
        to_delete = []
        for mid, entry in self._store.items():
            if entry["user_id"] != user_id:
                continue
            entry_scope = entry.get("scope", {})
            if all(entry_scope.get(k) == v for k, v in scope.items()):
                to_delete.append(mid)
        for mid in to_delete:
            del self._store[mid]
            self._history.pop(mid, None)
        return len(to_delete)

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy", backend="in_memory", latency_ms=0.0)

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
        entry = self._store.get(memory_id)
        if entry is None or entry["user_id"] != user_id:
            return None
        return _entry_to_memory(memory_id, entry)

    async def update(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        author: str | None = None,
    ) -> Memory:
        entry = self._store.get(memory_id)
        if entry is None or entry["user_id"] != user_id:
            raise MemoryAPIError(404, "Not found")
        now = datetime.now(UTC).isoformat()
        old_content = entry["content"]
        entry["content"] = content
        entry["updated_at"] = now
        if metadata is not None or author is not None:
            new_metadata = dict(metadata) if metadata is not None else dict(entry["metadata"])
            if author is not None:
                new_metadata[AUTHOR_METADATA_KEY] = author
            entry["metadata"] = new_metadata
        if memory_id in self._history:
            self._history[memory_id].append(
                {
                    "action": "updated",
                    "timestamp": now,
                    "content_before": old_content,
                    "content_after": content,
                    "author": author,
                }
            )
        return _entry_to_memory(memory_id, entry)

    async def list_memories(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        memories = []
        for mid, entry in self._store.items():
            if entry["user_id"] != user_id:
                continue
            if scope:
                entry_scope = entry.get("scope", {})
                if not all(entry_scope.get(k) == v for k, v in scope.items()):
                    continue
            memories.append(_entry_to_memory(mid, entry))
        return paginate(memories, cursor, limit)

    async def history(self, user_id: str, memory_id: str) -> list[HistoryEntry]:
        entry = self._store.get(memory_id)
        if entry is None or entry["user_id"] != user_id:
            raise MemoryAPIError(404, "Not found")
        raw = self._history.get(memory_id, [])
        return [
            HistoryEntry(
                action=h["action"],
                timestamp=h["timestamp"],
                content_before=h.get("content_before"),
                content_after=h.get("content_after"),
                author=h.get("author"),
            )
            for h in raw
        ]

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
        self._store.clear()
        self._history.clear()
