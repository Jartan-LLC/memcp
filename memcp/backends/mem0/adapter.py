"""mem0 REST API adapter: memcp's backend contract mapped onto mem0's API.

Talks to a self-hosted mem0 instance through `Mem0Client`. All mem0-specific
workarounds (flat filters, null-as-not-found, 5xx-on-malformed-id) live here.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from memcp.backends.base import MemoryBackend
from memcp.backends.mem0.client import Mem0Client
from memcp.types import (
    SYSTEM_METADATA_KEY,
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
    MemoryAPIError,
    System,
    is_wildcard,
    paginate,
    reject_nested_filters,
    scope_text,
    strip_reserved_metadata,
)

logger = logging.getLogger(__name__)

# mem0's GET /memories takes top_k, defaults it to 20, and refuses anything above
# 1000 (ALL_MEMORIES_LIMIT in its server). This is the most memories one call can
# see, and therefore the most an export of a mem0 tenant can contain.
LIST_CEILING = 1000


def _concrete(value: Any) -> Any:
    """Refuse a value mem0 would read as "any"; mem0 has no way to match it literally."""
    if is_wildcard(value):
        raise MemoryAPIError(400, "scope values must not be empty, '*' or null")
    return value


def _scope_items(scope: dict[str, Any]) -> list[tuple[str, Any]]:
    """A scope's keys and values minus user_id, which comes from the token; raises on nesting."""
    reject_nested_filters(scope)
    return [(key, val) for key, val in scope.items() if key != "user_id"]


def _build_search_filters(
    partition: str,
    scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flat filter dict for POST /search."""
    filters: dict[str, Any] = {"user_id": partition}
    if scope:
        for key, val in _scope_items(scope):
            filters[key] = scope_text(_concrete(val))
    return filters


def _build_identifier_params(
    partition: str,
    scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Query params for GET /memories and DELETE /memories."""
    params: dict[str, Any] = {"user_id": partition}
    if scope:
        for key, val in _scope_items(scope):
            params[key] = scope_text(_concrete(val))
    return params


def _split_system(stored: dict[str, Any]) -> tuple[System, dict[str, Any]]:
    """The system fields kept under SYSTEM_METADATA_KEY, and the rest without reserved keys."""
    raw = stored.get(SYSTEM_METADATA_KEY)
    # A malformed value is kept as the space, not dropped, so the space check fails on
    # it instead of passing on the partition alone.
    fields: dict[str, Any] = (
        raw if isinstance(raw, dict) else {} if raw is None else {"space": raw}
    )
    system = System(
        **{
            k: v
            for k, v in fields.items()
            if k in System.__annotations__ and (isinstance(v, str) or k == "space")
        }
    )
    return system, strip_reserved_metadata(stored) or {}


def _parse_memory(raw: dict[str, Any], *, score: float | None = None) -> Memory:
    """Convert mem0's response shape to canonical Memory."""
    system, metadata = _split_system(raw.get("metadata") or {})
    return Memory(
        id=raw.get("id", ""),
        content=raw.get("memory", raw.get("text", "")),
        score=score if score is not None else raw.get("score"),
        scope={k: v for k, v in raw.items() if k in ("agent_id", "run_id") and v is not None},
        metadata=metadata,
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at"),
        partition=raw.get("user_id") or "",
        system=system,
    )


def _earliest_and_latest(values: list[str | None]) -> tuple[str | None, str | None]:
    """The earliest and latest of some mem0 timestamps, skipping missing or invalid ones."""
    dated: list[tuple[datetime, str]] = []
    for value in values:
        try:
            parsed = datetime.fromisoformat(value or "")
        except ValueError:
            continue
        dated.append((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC), value or ""))
    if not dated:
        return None, None
    return min(dated)[1], max(dated)[1]


def _entity_rows(partition: str, memories: list[Memory]) -> list[dict[str, Any]]:
    """Count one tenant's memories per user, agent and run, in mem0's entity row shape."""
    groups: dict[tuple[str, str], list[Memory]] = {}
    for memory in memories:
        groups.setdefault(("user", partition), []).append(memory)
        for entity_type, key in (("agent", "agent_id"), ("run", "run_id")):
            if memory.scope.get(key):
                groups.setdefault((entity_type, str(memory.scope[key])), []).append(memory)
    rows: list[dict[str, Any]] = []
    # The user row first, so a `limit` cuts agent and run rows before it.
    for entity_type, entity_id in sorted(groups, key=lambda k: (k[0] != "user", k)):
        group = groups[(entity_type, entity_id)]
        created_at, _ = _earliest_and_latest([m.created_at for m in group])
        _, updated_at = _earliest_and_latest([m.updated_at or m.created_at for m in group])
        rows.append(
            {
                "id": entity_id,
                "type": entity_type,
                "total_memories": len(group),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return rows


class Mem0Backend(MemoryBackend):
    """Adapter for self-hosted mem0 REST API."""

    # mem0 runs content through an LLM on add(infer=True) and may store nothing.
    extracts_facts = True
    retrieval = "semantic"
    list_ceiling: int = LIST_CEILING

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0):
        self._client = Mem0Client(base_url, api_key, timeout=timeout)
        # Unlike sqlite, this backend has no local file to run a one-time cleanse
        # migration against — memcp has no way to enumerate every tenant a mem0
        # install holds, only whichever a request addresses. A row written by a
        # server version that did not validate `metadata` may carry a
        # caller-planted reserved key that reads back as server-set. See
        # docs/reference.md.
        logger.warning(
            "mem0 backend: memory attribution (`author`) is only as "
            "trustworthy as this store's write history. memcp has no migration "
            "hook for mem0 — run a metadata sweep on it before trusting "
            "attribution if any client could have written to it before this "
            "server version was deployed."
        )

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
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "user_id": partition,
            "infer": infer,
        }
        if scope:
            for key, val in _scope_items(scope):
                payload[key] = scope_text(_concrete(val))
        # Stripped here, not just by policy — see in_memory.add()'s
        # comment on the same line.
        stored_metadata = strip_reserved_metadata(metadata) or {}
        if system:
            stored_metadata[SYSTEM_METADATA_KEY] = dict(system)
        if stored_metadata:
            payload["metadata"] = stored_metadata

        result = await self._client.request("POST", "/memories", json=payload)
        if not isinstance(result, dict):
            logger.warning("Unexpected mem0 POST response shape: %s", type(result).__name__)
            return []
        results = result.get("results", [])
        if not results:
            return []
        return [
            AddResult(
                id=r["id"],
                status="ready",
                created_at=r.get("created_at", ""),
            )
            for r in results
        ]

    async def search(
        self,
        partition: str,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]:
        payload = {
            "query": query,
            "filters": _build_search_filters(partition, scope),
            "top_k": limit,
            "threshold": threshold,
        }
        result = await self._client.request("POST", "/search", json=payload)
        raw_results = (result or {}).get("results", []) if isinstance(result, dict) else []
        return [_parse_memory(r, score=r.get("score")) for r in raw_results]

    async def delete(self, partition: str, memory_id: str) -> bool:
        # Fetch-then-verify: mem0 DELETE is global, so check ownership first
        existing = await self.get(partition, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Not found")
        await self._client.request("DELETE", f"/memories/{memory_id}")
        return True

    async def delete_all(self, partition: str, scope: dict[str, Any]) -> int | None:
        # mem0 ignores a key it doesn't filter on, and httpx sends an empty list as
        # nothing; either widens the delete past the scope asked for. A wildcard value
        # is refused when the params are built. Only an empty scope reaches the whole tenant.
        if any(
            k not in self.scope_keys() or not isinstance(v, (str, int, float))
            for k, v in scope.items()
        ):
            raise MemoryAPIError(
                400,
                f"delete_all scope keys must be among {self.scope_keys()}, "
                "each with one string or number value",
            )
        params = _build_identifier_params(partition, scope)
        await self._client.request("DELETE", "/memories", params=params)
        return None  # mem0 doesn't return a count

    async def health(self) -> HealthStatus:
        start = time.monotonic()
        try:
            await self._client.request("GET", "/memories", params={"user_id": "__health_check__"})
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(status="healthy", backend="mem0", latency_ms=round(latency, 1))
        except Exception:
            logger.warning("Health check failed", exc_info=True)
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(status="unhealthy", backend="mem0", latency_ms=round(latency, 1))

    def capabilities(self) -> set[str]:
        return {"update", "list", "history", "entities"}

    def scope_keys(self) -> list[str]:
        return ["agent_id", "run_id"]

    async def get(self, partition: str, memory_id: str) -> Memory | None:
        result = await self._client.request("GET", f"/memories/{memory_id}")
        if result is None:
            return None
        if result.get("user_id") != partition:
            return None
        return _parse_memory(result)

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
        # Fetch-then-verify: mem0 PUT is global, check ownership first. `existing`
        # also supplies whichever of metadata and system the caller didn't pass, so
        # the PUT never drops either.
        existing = await self.get(partition, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Memory not found")
        body: dict[str, Any] = {"text": content}
        if metadata is not None or system is not None:
            base = strip_reserved_metadata(metadata if metadata is not None else existing.metadata)
            stored = base or {}
            kept = existing.system if system is None else system
            if kept:
                stored[SYSTEM_METADATA_KEY] = dict(kept)
            body["metadata"] = stored
        await self._client.request("PUT", f"/memories/{memory_id}", json=body)
        # mem0 PUT returns {"message": "..."}, not the memory. Fetch it.
        updated = await self.get(partition, memory_id)
        if updated is None:
            raise MemoryAPIError(503, "Update succeeded but read-back failed")
        return updated

    async def list_memories(
        self,
        partition: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        params = _build_identifier_params(partition, scope)
        # mem0 defaults top_k to 20, which would hide the rest from list, export and the
        # import dedup index. The list ceiling is mem0's own maximum; it rejects more.
        params["top_k"] = self.list_ceiling
        result = await self._client.request("GET", "/memories", params=params)
        raw = result if isinstance(result, list) else (result or {}).get("results", [])
        # A full response can't tell 1000 memories from more, so it counts as incomplete.
        at_ceiling = len(raw) >= self.list_ceiling
        if at_ceiling:
            logger.warning(
                "mem0 returned its ceiling of %d memories for user %s; memories beyond "
                "it can't be listed, so the result is marked incomplete",
                self.list_ceiling,
                partition,
            )
        memories = [_parse_memory(r) for r in raw]
        page = paginate(memories, cursor, limit)
        page.incomplete = at_ceiling
        return page

    async def history(self, partition: str, memory_id: str) -> list[HistoryEntry]:
        # Fetch-then-verify: mem0 history endpoint is global
        existing = await self.get(partition, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Not found")
        result = await self._client.request("GET", f"/memories/{memory_id}/history")
        if not result:
            return []
        # mem0's history log is entirely upstream-managed and carries no metadata
        # per event, so there is nowhere to read a per-event author from — unlike
        # in_memory/sqlite, which own their history log and record one directly.
        # The memory's current `.author` (from _parse_memory) still reflects its
        # last writer; only the event-by-event trail cannot be attributed here.
        return [
            HistoryEntry(
                action=entry.get("event", "unknown").lower(),
                timestamp=entry.get("created_at", ""),
                content_before=entry.get("old_memory"),
                content_after=entry.get("new_memory"),
                author=None,
            )
            for entry in result
        ]

    async def entities(
        self,
        partition: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> EntitiesResult:
        # Not mem0's GET /entities: it counts every tenant's user, agent and run
        # values together, so another tenant's writes would show up here.
        listed = await self.list_memories(partition, scope=scope, limit=self.list_ceiling)
        return EntitiesResult(
            entities=_entity_rows(partition, listed.memories)[:limit],
            incomplete=listed.incomplete,
        )

    # --- lifecycle ---

    async def close(self) -> None:
        await self._client.close()
