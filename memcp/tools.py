"""MCP tool registration — closure-based dependency injection.

All tool handlers are defined inside register_tools() as inner functions.
The closure captures backend and config, eliminating module-level globals.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from memcp.auth import get_tenant
from memcp.backend import MemoryBackend
from memcp.config import Config
from memcp.types import (
    MAX_EXPORT,
    MAX_IMPORT,
    MAX_SCOPE_KEY_LENGTH,
    MAX_SCOPE_KEYS,
    MAX_SCOPE_VALUE_LENGTH,
    NOT_FOUND_MSG,
    MemoryAPIError,
    canonical_error,
    reject_nested_filters,
    validate_content,
    validate_limit,
    validate_memory_id,
    validate_query,
)

logger = logging.getLogger(__name__)

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
DESTRUCTIVE = {"destructiveHint": True}


def _log_tool_call(fn: Any) -> Any:
    """Decorator that logs tool name, duration, and status."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        try:
            result = await fn(*args, **kwargs)
            duration_ms = (time.monotonic() - start) * 1000
            status = "error" if isinstance(result, dict) and "error" in result else "ok"
            logger.info(
                "tool=%s status=%s duration_ms=%.1f",
                fn.__name__,
                status,
                duration_ms,
            )
            return result
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "tool=%s status=exception duration_ms=%.1f",
                fn.__name__,
                duration_ms,
                exc_info=True,
            )
            raise

    return wrapper


def _backend_error(e: MemoryAPIError) -> dict[str, Any]:
    """Map a MemoryAPIError to the appropriate canonical error."""
    if e.status == 408:
        return canonical_error("timeout", str(e), retry=True)
    return canonical_error("backend_error", str(e), retry=e.status >= 500)


def register_tools(mcp: Any, backend: MemoryBackend, config: Config) -> None:
    """Register all MCP tools on the given server instance."""

    allowed_scope_keys = set(backend.scope_keys())

    # Wrap mcp.tool to auto-apply request logging
    _original_tool = mcp.tool

    def _logged_tool(**kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            return _original_tool(**kwargs)(_log_tool_call(fn))

        return decorator

    mcp.tool = _logged_tool  # type: ignore[assignment]

    # --- universal tools ---

    @mcp.tool(
        description=(
            "Store a fact/preference/decision. Extracts salient facts by "
            "default (may store nothing); infer=false for verbatim. "
            "Bulk: use import_memories."
        )
    )
    async def add_memory(
        content: str,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> Any:
        try:
            validate_content(content)
        except ValueError as e:
            return canonical_error("validation_error", str(e))
        user_id = get_tenant()
        try:
            scope = _validate_scope(scope, allowed_scope_keys)
        except _ScopeError as e:
            return e.error
        try:
            result = await backend.add(
                user_id, content, scope=scope, metadata=metadata, infer=infer
            )
        except MemoryAPIError as e:
            return _backend_error(e)

        if not result:
            return (
                "No durable fact was extracted, so nothing was stored. If you intended "
                "to store this exactly as written, call add_memory again with infer=false."
            )
        return _serialize_add_result(result)

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Semantic search ranked by relevance. threshold: minimum "
            "similarity (0-1). Unranked browsing: use list_memories."
        ),
    )
    async def search_memory(
        query: str,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> Any:
        try:
            validate_query(query)
            validate_limit(limit)
            if not (0.0 <= threshold <= 1.0):
                raise ValueError("threshold must be between 0.0 and 1.0")
        except ValueError as e:
            return canonical_error("validation_error", str(e))
        user_id = get_tenant()
        try:
            scope = _validate_scope(scope, allowed_scope_keys)
        except _ScopeError as e:
            return e.error
        try:
            results = await backend.search(
                user_id, query, scope=scope, limit=limit, threshold=threshold
            )
        except MemoryAPIError as e:
            return _backend_error(e)

        return {"results": [_serialize_memory(m) for m in results]}

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=("Delete one memory by memory_id. Confirm with user first."),
    )
    async def delete_memory(memory_id: str) -> Any:
        user_id = get_tenant()
        try:
            validate_memory_id(memory_id)
        except ValueError as e:
            return canonical_error("validation_error", str(e))
        try:
            result = await backend.delete(user_id, memory_id)
        except MemoryAPIError as e:
            if e.status in (404, 410):
                return canonical_error("not_found", NOT_FOUND_MSG)
            return _backend_error(e)
        return {"deleted": result}

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Bulk-delete memories matching a scope (e.g. agent_id, run_id). "
            "Deletes by scope structure, not content. Requires at least one scope "
            "key. Confirm with user first."
        ),
    )
    async def delete_all_memories(scope: dict[str, Any]) -> Any:
        user_id = get_tenant()
        try:
            cleaned = _validate_scope(scope, allowed_scope_keys)
        except _ScopeError as e:
            return e.error
        if not cleaned:
            return canonical_error(
                "scope_required",
                "delete_all_memories requires at least one scope key.",
            )
        try:
            count = await backend.delete_all(user_id, cleaned)
        except MemoryAPIError as e:
            return _backend_error(e)
        return {"deleted_count": count}

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Returns server version, backend type, capabilities, and valid "
            "scope keys. No memory content."
        ),
    )
    async def memory_status() -> dict[str, Any]:
        return {
            "backend": config.backend_name,
            "version": config.version,
            "capabilities": sorted(backend.capabilities()),
            "scope_keys": backend.scope_keys(),
        }

    # --- optional tools (registered if backend declares capability) ---

    caps = backend.capabilities()

    if "list_memories" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Export all memories as JSON. For backup/migration — output "
                "compatible with import_memories. Browsing: list_memories; "
                "search: search_memory."
            ),
        )
        async def export_memories() -> Any:
            user_id = get_tenant()
            try:
                result = await backend.list_memories(user_id, limit=MAX_EXPORT + 1)
            except MemoryAPIError as e:
                return _backend_error(e)
            truncated = len(result.memories) > MAX_EXPORT
            memories = result.memories[:MAX_EXPORT]
            return {
                "memories": [_serialize_memory(m) for m in memories],
                "count": len(memories),
                "truncated": truncated,
            }

        @mcp.tool(
            description=(
                "Batch-import from JSON array. Each entry needs 'content'; "
                "optional 'scope'/'metadata'. Stored verbatim (no extraction). "
                "Deduped by exact content match (scope-independent). "
                "on_conflict: skip (default), overwrite, duplicate."
            ),
        )
        async def import_memories(
            memories: list[dict[str, Any]],
            on_conflict: str = "skip",
        ) -> Any:
            if not memories:
                return canonical_error("validation_error", "memories array must not be empty")
            if len(memories) > MAX_IMPORT:
                return canonical_error(
                    "validation_error",
                    f"Too many memories to import (limit {MAX_IMPORT})",
                )
            if on_conflict not in ("skip", "overwrite", "duplicate"):
                return canonical_error(
                    "validation_error",
                    "on_conflict must be 'skip', 'overwrite', or 'duplicate'",
                )
            if on_conflict == "overwrite" and "update_memory" not in caps:
                return canonical_error(
                    "not_supported",
                    "on_conflict='overwrite' requires update_memory capability",
                )

            user_id = get_tenant()

            # Build dedup index from existing memories (capped at MAX_EXPORT;
            # users with >10k memories get best-effort dedup)
            existing: dict[str, str] = {}
            if on_conflict != "duplicate":
                try:
                    result = await backend.list_memories(user_id, limit=MAX_EXPORT + 1)
                    existing = {m.content: m.id for m in result.memories}
                except MemoryAPIError as e:
                    return _backend_error(e)

            imported = []
            skipped = []
            errors = []

            for i, entry in enumerate(memories):
                content = entry.get("content")
                if not isinstance(content, str):
                    errors.append({"index": i, "error": "missing or invalid content"})
                    continue
                try:
                    validate_content(content)
                except ValueError as e:
                    errors.append({"index": i, "error": str(e)})
                    continue

                scope = entry.get("scope")
                if scope:
                    try:
                        scope = _validate_scope(scope, allowed_scope_keys)
                    except _ScopeError as e:
                        errors.append({"index": i, "error": e.error["error"]["message"]})
                        continue
                metadata = entry.get("metadata")
                dup_id = existing.get(content)

                if dup_id and on_conflict == "skip":
                    skipped.append({"index": i, "existing_id": dup_id})
                    continue

                if dup_id and on_conflict == "overwrite":
                    try:
                        await backend.update(user_id, dup_id, content, metadata=metadata)
                        imported.append({"id": dup_id, "index": i, "action": "updated"})
                    except MemoryAPIError as e:
                        errors.append({"index": i, "error": str(e)})
                    continue

                try:
                    result = await backend.add(
                        user_id, content, scope=scope, metadata=metadata, infer=False
                    )
                    if result:
                        items = result if isinstance(result, list) else [result]
                        for r in items:
                            imported.append({"id": r.id, "index": i, "action": "created"})
                            existing[content] = r.id
                except MemoryAPIError as e:
                    errors.append({"index": i, "error": str(e)})

            return {
                "imported": len(imported),
                "skipped": len(skipped),
                "errors": errors,
                "results": imported,
                "skipped_details": skipped,
            }

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Browse memories, optionally filtered by scope. Unranked, "
                "paginated. Semantic queries: use search_memory."
            ),
        )
        async def list_memories(
            scope: dict[str, Any] | None = None,
            limit: int = 100,
            cursor: str | None = None,
        ) -> Any:
            try:
                validate_limit(limit)
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            user_id = get_tenant()
            try:
                scope = _validate_scope(scope, allowed_scope_keys)
            except _ScopeError as e:
                return e.error
            try:
                result = await backend.list_memories(
                    user_id, scope=scope, limit=limit, cursor=cursor
                )
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            except MemoryAPIError as e:
                return _backend_error(e)
            return {
                "memories": [_serialize_memory(m) for m in result.memories],
                "next_cursor": result.next_cursor,
            }

    if "get_memory" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description="Fetch a single memory by ID. Returns full content, scope, and metadata.",
        )
        async def get_memory(memory_id: str) -> Any:
            user_id = get_tenant()
            try:
                validate_memory_id(memory_id)
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            try:
                result = await backend.get(user_id, memory_id)
            except MemoryAPIError as e:
                if e.status in (404, 410):
                    return canonical_error("not_found", NOT_FOUND_MSG)
                return _backend_error(e)
            if result is None:
                return canonical_error("not_found", NOT_FOUND_MSG)
            return _serialize_memory(result)

    if "update_memory" in caps:

        @mcp.tool(
            annotations={"idempotentHint": True, "destructiveHint": True},
            description=(
                "Full-replace a memory's content by memory_id (not a patch). "
                "Scope is immutable — to change scope, add new + delete old."
            ),
        )
        async def update_memory(
            memory_id: str,
            content: str,
            metadata: dict[str, Any] | None = None,
        ) -> Any:
            user_id = get_tenant()
            try:
                validate_memory_id(memory_id)
                validate_content(content)
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            try:
                result = await backend.update(user_id, memory_id, content, metadata=metadata)
            except MemoryAPIError as e:
                if e.status in (404, 410):
                    return canonical_error("not_found", NOT_FOUND_MSG)
                return _backend_error(e)
            return _serialize_memory(result)

    if "memory_history" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Change log for a memory: timestamps and previous/current "
                "content per create/update event."
            ),
        )
        async def memory_history(memory_id: str) -> Any:
            user_id = get_tenant()
            try:
                validate_memory_id(memory_id)
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            try:
                entries = await backend.history(user_id, memory_id)
            except MemoryAPIError as e:
                if e.status in (404, 410):
                    return canonical_error("not_found", NOT_FOUND_MSG)
                return _backend_error(e)
            return {
                "history": [
                    {
                        "action": e.action,
                        "timestamp": e.timestamp,
                        "content_before": e.content_before,
                        "content_after": e.content_after,
                    }
                    for e in entries
                ]
            }

    if "memory_entities" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Knowledge graph: entities and relationships from memories. "
                "Not a search tool — use search_memory for topics."
            ),
        )
        async def memory_entities(
            scope: dict[str, Any] | None = None,
            limit: int = 100,
        ) -> Any:
            try:
                validate_limit(limit)
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            user_id = get_tenant()
            try:
                scope = _validate_scope(scope, allowed_scope_keys)
            except _ScopeError as e:
                return e.error
            try:
                result = await backend.entities(user_id, scope=scope, limit=limit)
            except MemoryAPIError as e:
                return _backend_error(e)
            return {
                "entities": result.entities,
                "relationships": result.relationships,
            }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ScopeError(Exception):
    def __init__(self, error: dict[str, Any]):
        self.error = error


def _validate_scope(scope: dict[str, Any] | None, allowed_keys: set[str]) -> dict[str, Any] | None:
    """Strip user_id, reject nested filters, validate scope keys."""
    if not scope:
        return scope
    if "user_id" in scope:
        scope = {k: v for k, v in scope.items() if k != "user_id"}
        logger.warning("Stripped user_id from scope dict; remaining keys: %s", list(scope.keys()))
    try:
        reject_nested_filters(scope)
    except ValueError as e:
        raise _ScopeError(canonical_error("nested_filter", str(e))) from e
    if len(scope) > MAX_SCOPE_KEYS:
        raise _ScopeError(
            canonical_error("validation_error", f"Scope has too many keys (max {MAX_SCOPE_KEYS})")
        )
    for k, v in scope.items():
        if len(k) > MAX_SCOPE_KEY_LENGTH:
            raise _ScopeError(
                canonical_error(
                    "validation_error",
                    f"Scope key too long: {k!r} (max {MAX_SCOPE_KEY_LENGTH})",
                )
            )
        if v is not None and not isinstance(v, (str, int, float, bool)):
            raise _ScopeError(
                canonical_error(
                    "validation_error",
                    f"Scope value for {k!r} must be a string or number, got {type(v).__name__}",
                )
            )
        if isinstance(v, str) and len(v) > MAX_SCOPE_VALUE_LENGTH:
            raise _ScopeError(
                canonical_error(
                    "validation_error",
                    f"Scope value too long for {k!r} (max {MAX_SCOPE_VALUE_LENGTH})",
                )
            )
    unknown = set(scope) - allowed_keys
    if unknown:
        raise _ScopeError(
            canonical_error(
                "invalid_scope",
                f"Unknown scope keys: {sorted(unknown)}. Valid keys: {sorted(allowed_keys)}",
            )
        )
    return scope


def _serialize_memory(m: Any) -> dict[str, Any]:
    return {
        "id": m.id,
        "content": m.content,
        "score": m.score,
        "scope": m.scope,
        "metadata": m.metadata,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


def _serialize_add_result(result: Any) -> Any:
    if isinstance(result, list):
        return {
            "results": [
                {"id": r.id, "status": r.status, "created_at": r.created_at} for r in result
            ]
        }
    return {"id": result.id, "status": result.status, "created_at": result.created_at}
