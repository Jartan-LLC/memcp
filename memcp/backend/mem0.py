"""mem0 REST API adapter.

Talks to a self-hosted mem0 instance. All mem0-specific workarounds
(flat filters, null-as-not-found, 5xx-on-malformed-id) live here.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

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

logger = logging.getLogger(__name__)

# mem0's GET /memories takes top_k, defaults it to 20, and refuses anything above
# 1000 (ALL_MEMORIES_LIMIT in its server). This is the most memories one call can
# see, and therefore the most an export of a mem0 tenant can contain.
LIST_CEILING = 1000

# The entity types mem0's GET /entities buckets by, and the payload field each one
# reads. Same mapping as its TYPE_TO_FIELD, because entities() reproduces that
# bucketing over the caller's own memories.
ENTITY_FIELDS = (("user", "user_id"), ("agent", "agent_id"), ("run", "run_id"))


def _norm(value: Any) -> Any:
    """Return None for wildcard/empty sentinels; pass through otherwise."""
    if isinstance(value, str) and value.strip() in ("", "*"):
        return None
    return value


def _caller_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a caller's scope and drop user_id from it.

    Every builder below writes scope keys over a dict that already holds the
    token-derived user_id, so a scope carrying user_id would overwrite the tenant.
    Nothing reaches them that way today — tools.py strips it — but that is one
    check at the far end from the query, and tenant isolation on this backend is
    post-hoc rather than structural. Dropping it here removes the class
    (SEC-2026-0065).
    """
    if not scope:
        return {}
    reject_nested_filters(scope)
    if "user_id" in scope:
        logger.warning("Dropped user_id from a backend scope dict; it comes from the token")
        return {k: v for k, v in scope.items() if k != "user_id"}
    return scope


def _build_search_filters(
    user_id: str,
    scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flat filter dict for POST /search."""
    filters: dict[str, Any] = {"user_id": user_id}
    for key, val in _caller_scope(scope).items():
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
    for key, val in _caller_scope(scope).items():
        val = _norm(val)
        if val is not None:
            params[key] = val
    return params


def _parse_ts(value: Any) -> datetime | None:
    """Parse a mem0 timestamp; naive values are read as UTC so any two compare."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _bucket_entities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket memory rows into entity rows, the way mem0's GET /entities does.

    Same shape and same aggregation — count per bucket, earliest created_at,
    latest updated_at — over whichever rows it is given. Timestamps are echoed
    back as the strings the memories carry, so an entity row's dates match the
    dates the same memories report through get/list.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        created = _parse_ts(row.get("created_at"))
        updated = _parse_ts(row.get("updated_at")) or created
        for entity_type, field in ENTITY_FIELDS:
            value = row.get(field)
            if not value:
                continue
            bucket = buckets.setdefault(
                (entity_type, str(value)),
                {"total_memories": 0, "created_at": None, "updated_at": None},
            )
            bucket["total_memories"] += 1
            if created and (bucket["created_at"] is None or created < bucket["created_at"][0]):
                bucket["created_at"] = (created, row.get("created_at"))
            if updated and (bucket["updated_at"] is None or updated > bucket["updated_at"][0]):
                bucket["updated_at"] = (updated, row.get("updated_at") or row.get("created_at"))
    return [
        {
            "id": entity_id,
            "type": entity_type,
            "total_memories": data["total_memories"],
            "created_at": data["created_at"][1] if data["created_at"] else None,
            "updated_at": data["updated_at"][1] if data["updated_at"] else None,
        }
        for (entity_type, entity_id), data in sorted(buckets.items())
    ]


def _parse_memory(raw: dict[str, Any], *, score: float | None = None) -> Memory:
    """Convert mem0's response shape to canonical Memory."""
    return Memory(
        id=raw.get("id", ""),
        content=raw.get("memory", raw.get("text", "")),
        score=score if score is not None else raw.get("score"),
        scope={k: v for k, v in raw.items() if k in ("agent_id", "run_id") and v is not None},
        metadata=raw.get("metadata") or {},
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
    ) -> list[AddResult]:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "user_id": user_id,
            "infer": infer,
        }
        for key, val in _caller_scope(scope).items():
            normed = _norm(val)
            if normed is not None:
                payload[key] = normed
        if metadata:
            payload["metadata"] = metadata

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
    ) -> Memory:
        # Fetch-then-verify: mem0 PUT is global, check ownership first
        existing = await self.get(user_id, memory_id)
        if existing is None:
            raise MemoryAPIError(404, "Memory not found")
        body: dict[str, Any] = {"text": content}
        if metadata is not None:
            body["metadata"] = metadata
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
        return [
            HistoryEntry(
                action=entry.get("event", "unknown").lower(),
                timestamp=entry.get("created_at", ""),
                content_before=entry.get("old_memory"),
                content_after=entry.get("new_memory"),
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
        """Entity buckets over this tenant's own memories.

        Not GET /entities. That endpoint is global: it scans every payload in the
        store and buckets each one by user_id, agent_id and run_id, so a bucket
        exists for every value any tenant ever wrote and carries that bucket's
        count and timestamps. Nothing in a row says who owns it — the id is the
        only identifier, and agent_id/run_id are caller-controlled — so filtering
        the response on `id == user_id` let one tenant put a row into another's
        output, and let a value two tenants share count both their memories
        (SEC-2026-0065). No refinement of that filter can fix it, because the
        ownership is not in the response. Bucketing the caller's own memory
        listing gives the same rows from data that is already tenant-scoped on the
        wire, so isolation here is the query rather than a filter over the answer.
        """
        params = _build_identifier_params(user_id, scope)
        params["top_k"] = LIST_CEILING
        result = await self._request("GET", "/memories", params=params)
        raw = result if isinstance(result, list) else (result or {}).get("results", [])
        if len(raw) >= LIST_CEILING:
            logger.warning(
                "mem0 returned its ceiling of %d memories for user %s; entity counts "
                "for this tenant are undercounts",
                LIST_CEILING,
                user_id,
            )
        return EntitiesResult(entities=_bucket_entities(raw)[:limit])

    # --- lifecycle ---

    async def close(self) -> None:
        await self._http.aclose()
