"""mem0 REST API adapter.

Talks to a self-hosted mem0 instance. All mem0-specific workarounds
(flat filters, null-as-not-found, 5xx-on-malformed-id) live here.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

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

logger = logging.getLogger(__name__)

# mem0's GET /memories takes top_k, defaults it to 20, and refuses anything above
# 1000 (ALL_MEMORIES_LIMIT in its server). This is the most memories one call can
# see, and therefore the most an export of a mem0 tenant can contain.
LIST_CEILING = 1000


def _norm(value: Any) -> Any:
    """Return None for wildcard/empty sentinels; pass through otherwise."""
    if isinstance(value, str) and value.strip() in ("", "*"):
        return None
    return value


def _build_search_filters(
    user_id: str,
    scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flat filter dict for POST /search."""
    filters: dict[str, Any] = {"user_id": user_id}
    if scope:
        reject_nested_filters(scope)
        for key, val in scope.items():
            val = _norm(val) if isinstance(val, str) else val
            if val is not None:
                filters[key] = val
    return filters


def _build_identifier_params(
    user_id: str,
    scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Query params for GET /memories and DELETE /memories."""
    params: dict[str, Any] = {"user_id": user_id}
    if scope:
        reject_nested_filters(scope)
        for key, val in scope.items():
            val = _norm(val)
            if val is not None:
                params[key] = val
    return params


def _parse_memory(raw: dict[str, Any], *, score: float | None = None) -> Memory:
    """Convert mem0's response shape to canonical Memory."""
    author, metadata = split_author(raw.get("metadata") or {})
    return Memory(
        id=raw.get("id", ""),
        content=raw.get("memory", raw.get("text", "")),
        score=score if score is not None else raw.get("score"),
        scope={k: v for k, v in raw.items() if k in ("agent_id", "run_id") and v is not None},
        metadata=metadata,
        author=author,
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at"),
    )


class Mem0Backend(MemoryBackend):
    # mem0 runs content through an LLM on add(infer=True) and may store nothing.
    extracts_facts = True

    """Adapter for self-hosted mem0 REST API."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=httpx.Timeout(timeout),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
        # Unlike sqlite, this backend has no local file to run a one-time cleanse
        # migration against (Thorne, JAR-723 finding 1) — memcp has no way to
        # enumerate every tenant a mem0 install holds, only whichever a request
        # addresses. A row written before every server that has ever talked to
        # this store validated `metadata` may carry a caller-planted reserved
        # key that reads back as server-attributed. See docs/reference.md.
        logger.warning(
            "mem0 backend: memory attribution (`author`/`attributed`) is only as "
            "trustworthy as this store's write history. memcp has no migration "
            "hook for mem0 — run a metadata sweep on it before trusting "
            "attribution if any client could have written to it before this "
            "server version was deployed."
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            resp = await self._http.request(method, path, params=params, json=json)
        except httpx.TimeoutException as e:
            raise MemoryAPIError(408, f"Timeout: {e}") from e
        except httpx.RequestError as e:
            raise MemoryAPIError(503, f"Network error: {e}") from e
        if resp.status_code >= 400:
            raise MemoryAPIError(resp.status_code, resp.text)
        return resp.json() if resp.content else None

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
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "user_id": user_id,
            "infer": infer,
        }
        if scope:
            reject_nested_filters(scope)
            for key, val in scope.items():
                normed = _norm(val)
                if normed is not None:
                    payload[key] = normed
        # Stripped here, not just by the tool layer — see in_memory.add()'s
        # comment on the same line.
        stored_metadata = strip_reserved_metadata(metadata) or {}
        if author is not None:
            stored_metadata[AUTHOR_METADATA_KEY] = author
        if stored_metadata:
            payload["metadata"] = stored_metadata

        result = await self._request("POST", "/memories", json=payload)
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
        user_id: str,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]:
        payload = {
            "query": query,
            "filters": _build_search_filters(user_id, scope),
            "top_k": limit,
            "threshold": threshold,
        }
        result = await self._request("POST", "/search", json=payload)
        raw_results = (result or {}).get("results", []) if isinstance(result, dict) else []
        return [_parse_memory(r, score=r.get("score")) for r in raw_results]

    async def delete(self, user_id: str, memory_id: str) -> bool:
        # Fetch-then-verify: mem0 DELETE is global, so check ownership first
        existing = await self.get(user_id, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Not found")
        await self._request("DELETE", f"/memories/{memory_id}")
        return True

    async def delete_all(self, user_id: str, scope: dict[str, Any]) -> int | None:
        params = _build_identifier_params(user_id, scope)
        await self._request("DELETE", "/memories", params=params)
        return None  # mem0 doesn't return a count

    async def health(self) -> HealthStatus:
        start = time.monotonic()
        try:
            await self._request("GET", "/memories", params={"user_id": "__health_check__"})
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(status="healthy", backend="mem0", latency_ms=round(latency, 1))
        except Exception:
            logger.warning("Health check failed", exc_info=True)
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(status="unhealthy", backend="mem0", latency_ms=round(latency, 1))

    def capabilities(self) -> set[str]:
        return {
            "get_memory",
            "update_memory",
            "list_memories",
            "memory_history",
            "memory_entities",
        }

    def scope_keys(self) -> list[str]:
        return ["agent_id", "run_id"]

    # --- optional ---

    async def get(self, user_id: str, memory_id: str) -> Memory | None:
        result = await self._request("GET", f"/memories/{memory_id}")
        if result is None:
            return None
        if result.get("user_id") != user_id:
            return None
        return _parse_memory(result)

    async def update(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        author: str | None = None,
    ) -> Memory:
        # Fetch-then-verify: mem0 PUT is global, check ownership first. `existing`
        # also supplies the base metadata to re-stamp onto when the caller passes
        # no metadata of its own — mem0's PUT has no partial-merge semantics, so
        # sending a fresh metadata dict with only the new author would drop
        # whatever else was stored.
        existing = await self.get(user_id, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Memory not found")
        body: dict[str, Any] = {"text": content}
        if metadata is not None or author is not None:
            # existing.metadata is already reserved-key-free (it came back through
            # _parse_memory/split_author); a caller-supplied metadata still needs
            # the same strip _parse_memory would have applied.
            raw = metadata if metadata is not None else existing.metadata
            base = strip_reserved_metadata(raw) or {}
            if author is not None:
                base[AUTHOR_METADATA_KEY] = author
            body["metadata"] = base
        await self._request("PUT", f"/memories/{memory_id}", json=body)
        # mem0 PUT returns {"message": "..."}, not the memory. Fetch it.
        updated = await self.get(user_id, memory_id)
        if updated is None:
            raise MemoryAPIError(503, "Update succeeded but read-back failed")
        return updated

    async def list_memories(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        params = _build_identifier_params(user_id, scope)
        # Without an explicit top_k, mem0 returns its default of 20 — so list,
        # export and the import dedup index all silently saw only the first 20
        # memories. LIST_CEILING is the server's own maximum; it rejects more.
        params["top_k"] = LIST_CEILING
        result = await self._request("GET", "/memories", params=params)
        raw = result if isinstance(result, list) else (result or {}).get("results", [])
        if len(raw) >= LIST_CEILING:
            logger.warning(
                "mem0 returned its ceiling of %d memories for user %s; anything beyond "
                "it is not listed, so an export of this tenant is incomplete",
                LIST_CEILING,
                user_id,
            )
        memories = [_parse_memory(r) for r in raw]
        return paginate(memories, cursor, limit)

    async def history(self, user_id: str, memory_id: str) -> list[HistoryEntry]:
        # Fetch-then-verify: mem0 history endpoint is global
        existing = await self.get(user_id, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Not found")
        result = await self._request("GET", f"/memories/{memory_id}/history")
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
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> EntitiesResult:
        result = await self._request("GET", "/entities")
        raw = result if isinstance(result, list) else []
        # mem0 /entities ignores user_id param — post-filter for tenant isolation
        filtered = [e for e in raw if e.get("id") == user_id]
        if raw and not filtered:
            logger.warning(
                "Entities post-filter returned empty for user %s (%d raw entities)",
                user_id,
                len(raw),
            )
        return EntitiesResult(entities=filtered[:limit])

    # --- lifecycle ---

    async def close(self) -> None:
        await self._http.aclose()
