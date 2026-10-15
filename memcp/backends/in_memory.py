"""In-memory backend adapter — for conformance tests, dev mode, and demos.

Stores memories in plain dicts. No persistence, no extraction, no vector search.
Search is the keyword scorer in `memcp.backends.keyword`, shared with the sqlite
backend so the two rank identically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from memcp.types import (
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
    MemoryAPIError,
    System,
    paginate,
    reject_nested_filters,
    scope_as_text,
    scope_text,
    strip_reserved_metadata,
)

from .base import MemoryBackend
from .keyword import score as _score


def _entry_to_memory(mid: str, entry: dict[str, Any], *, score: float | None = None) -> Memory:
    return Memory(
        id=mid,
        content=entry["content"],
        score=score,
        scope=entry.get("scope", {}),
        metadata=dict(entry.get("metadata", {})),
        created_at=entry["created_at"],
        updated_at=entry.get("updated_at"),
        partition=entry["user_id"],
        system=System(**entry["system"]),
    )


class InMemoryBackend(MemoryBackend):
    """Trivial in-memory implementation of the MemoryBackend protocol."""

    retrieval = "keyword"

    def __init__(self) -> None:
        # {memory_id: {user_id, content, scope, metadata, system, created_at, updated_at}}
        self._store: dict[str, dict[str, Any]] = {}
        # {memory_id: [HistoryEntry, ...]}
        self._history: dict[str, list[dict[str, Any]]] = {}

    # --- required ---

    async def add(
        self,
        partition: str,
        content: str,
        *,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
        system: System | None = None,
    ) -> list[AddResult]:
        if scope:
            reject_nested_filters(scope)
        memory_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        # Reserved keys are stripped here as well as by policy: whoever calls this
        # method, a `_memcp_` key must never reach storage.
        self._store[memory_id] = {
            "user_id": partition,
            "content": content,
            "scope": scope_as_text(scope or {}),
            "metadata": strip_reserved_metadata(metadata) or {},
            "system": dict(system or {}),
            "created_at": now,
            "updated_at": None,
        }
        self._history[memory_id] = [
            {
                "action": "created",
                "timestamp": now,
                "content_before": None,
                "content_after": content,
                "author": (system or {}).get("author"),
            }
        ]
        return [AddResult(id=memory_id, status="ready", created_at=now)]

    async def search(
        self,
        partition: str,
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
            if entry["user_id"] != partition:
                continue
            if scope:
                entry_scope = entry.get("scope", {})
                if not all(entry_scope.get(k) == scope_text(v) for k, v in scope.items()):
                    continue
            relevance = _score(query, entry["content"])
            if relevance is None:
                continue
            results.append((relevance, mid, entry))
        results.sort(key=lambda x: x[0], reverse=True)
        return [_entry_to_memory(mid, entry, score=score) for score, mid, entry in results[:limit]]

    async def delete(self, partition: str, memory_id: str) -> bool:
        entry = self._store.get(memory_id)
        if entry is None:
            raise MemoryAPIError(404, "Not found")
        if entry["user_id"] != partition:
            raise MemoryAPIError(404, "Not found")
        del self._store[memory_id]
        self._history.pop(memory_id, None)
        return True

    async def delete_all(self, partition: str, scope: dict[str, Any]) -> int:
        reject_nested_filters(scope)
        to_delete = []
        for mid, entry in self._store.items():
            if entry["user_id"] != partition:
                continue
            entry_scope = entry.get("scope", {})
            if all(entry_scope.get(k) == scope_text(v) for k, v in scope.items()):
                to_delete.append(mid)
        for mid in to_delete:
            del self._store[mid]
            self._history.pop(mid, None)
        return len(to_delete)

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy", backend="in_memory", latency_ms=0.0)

    def capabilities(self) -> set[str]:
        # No entities: see entities() below.
        return {"update", "list", "history"}

    def scope_keys(self) -> list[str]:
        return ["agent_id", "run_id"]

    async def get(self, partition: str, memory_id: str) -> Memory | None:
        entry = self._store.get(memory_id)
        if entry is None or entry["user_id"] != partition:
            return None
        return _entry_to_memory(memory_id, entry)

    # --- optional ---

    async def update(
        self,
        partition: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        system: System | None = None,
    ) -> Memory:
        entry = self._store.get(memory_id)
        if entry is None or entry["user_id"] != partition:
            raise MemoryAPIError(404, "Not found")
        now = datetime.now(UTC).isoformat()
        old_content = entry["content"]
        entry["content"] = content
        entry["updated_at"] = now
        if metadata is not None:
            entry["metadata"] = strip_reserved_metadata(metadata) or {}
        if system is not None:
            entry["system"] = dict(system)
        if memory_id in self._history:
            self._history[memory_id].append(
                {
                    "action": "updated",
                    "timestamp": now,
                    "content_before": old_content,
                    "content_after": content,
                    "author": entry["system"].get("author"),
                }
            )
        return _entry_to_memory(memory_id, entry)

    async def list_memories(
        self,
        partition: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        memories = []
        for mid, entry in self._store.items():
            if entry["user_id"] != partition:
                continue
            if scope:
                entry_scope = entry.get("scope", {})
                if not all(entry_scope.get(k) == scope_text(v) for k, v in scope.items()):
                    continue
            memories.append(_entry_to_memory(mid, entry))
        return paginate(memories, cursor, limit)

    async def history(self, partition: str, memory_id: str) -> list[HistoryEntry]:
        entry = self._store.get(memory_id)
        if entry is None or entry["user_id"] != partition:
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
        partition: str,
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
