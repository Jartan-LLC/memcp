"""MCP tool registration — closure-based dependency injection.

All tool handlers are defined inside register_tools() as inner functions.
The closure captures backend and config, eliminating module-level globals.
"""

from __future__ import annotations

import logging
from typing import Any

from memcp.auth import get_tenant
from memcp.backend.base import MemoryBackend
from memcp.config import Config
from memcp.types import (
    MAX_SCOPE_KEY_LENGTH,
    MAX_SCOPE_KEYS,
    MAX_SCOPE_VALUE_LENGTH,
    NOT_FOUND_MSG,
    MemoryAPIError,
    canonical_error,
    validate_content,
    validate_limit,
    validate_memory_id,
    validate_query,
)

logger = logging.getLogger(__name__)

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
DESTRUCTIVE = {"destructiveHint": True}


def _backend_error(e: MemoryAPIError) -> dict[str, Any]:
    """Map a MemoryAPIError to the appropriate canonical error."""
    if e.status == 408:
        return canonical_error("timeout", str(e), retry=True)
    return canonical_error("backend_error", str(e), retry=e.status >= 500)


def register_tools(mcp: Any, backend: MemoryBackend, config: Config) -> None:
    """Register all MCP tools on the given server instance."""

    allowed_scope_keys = set(backend.scope_keys())

    # --- universal tools ---

    @mcp.tool(
        description=(
            "Store content in long-term memory. Use whenever a durable fact, "
            "preference, decision, or anything worth recalling later comes up. "
            "By default the server extracts salient facts and may store nothing "
            "if it finds none — set infer to false to store verbatim."
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
        except _InvalidScope as e:
            return e.error
        except ValueError as e:
            return canonical_error("nested_filter", str(e))
        try:
            result = await backend.add(
                user_id, content, scope=scope, metadata=metadata, infer=infer
            )
        except ValueError as e:
            return canonical_error("nested_filter", str(e))
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
            "Semantically search stored memories by meaning. Use before answering "
            "anything that depends on what's already known about the user. "
            "Returns memories ranked by relevance."
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
        except ValueError as e:
            return canonical_error("validation_error", str(e))
        user_id = get_tenant()
        try:
            scope = _validate_scope(scope, allowed_scope_keys)
        except _InvalidScope as e:
            return e.error
        except ValueError as e:
            return canonical_error("nested_filter", str(e))
        try:
            results = await backend.search(
                user_id, query, scope=scope, limit=limit, threshold=threshold
            )
        except ValueError as e:
            return canonical_error("nested_filter", str(e))
        except MemoryAPIError as e:
            return _backend_error(e)

        return {"results": [_serialize_memory(m) for m in results]}

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Delete a single memory by memory_id. Confirm with the user before deleting."
        ),
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
            "Delete every memory within a given scope. Requires at least one scope "
            "key — unscoped deletion is not allowed. Confirm with the user first."
        ),
    )
    async def delete_all_memories(scope: dict[str, Any]) -> Any:
        user_id = get_tenant()
        try:
            cleaned = _validate_scope(scope, allowed_scope_keys)
        except _InvalidScope as e:
            return e.error
        except ValueError as e:
            return canonical_error("nested_filter", str(e))
        if not cleaned:
            return canonical_error(
                "scope_required",
                "delete_all_memories requires at least one scope key.",
            )
        try:
            count = await backend.delete_all(user_id, cleaned)
        except ValueError as e:
            return canonical_error("nested_filter", str(e))
        except MemoryAPIError as e:
            return _backend_error(e)
        return {"deleted_count": count}

    @mcp.tool(
        annotations=READ_ONLY,
        description="Server and backend information.",
    )
    async def memory_status() -> dict[str, Any]:
        return {
            "backend": config.backend_name,
            "version": config.version,
            "capabilities": sorted(backend.capabilities()),
            "scope_keys": backend.scope_keys(),
        }

    MAX_EXPORT = 10_000

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Export all memories for backup or portability. Returns a JSON array "
            "of all memories. For browsing or searching, use list_memories or "
            "search_memory instead."
        ),
    )
    async def export_memories() -> Any:
        user_id = get_tenant()
        try:
            result = await backend.list_memories(user_id, limit=MAX_EXPORT)
        except MemoryAPIError as e:
            return _backend_error(e)
        if len(result.memories) >= MAX_EXPORT:
            return canonical_error(
                "validation_error",
                f"Too many memories to export (>{MAX_EXPORT}). Contact admin for bulk export.",
            )
        return {
            "memories": [_serialize_memory(m) for m in result.memories],
            "count": len(result.memories),
        }

    # --- optional tools (registered if backend declares capability) ---

    caps = backend.capabilities()

    if "get_memory" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description="Fetch a single memory by its memory_id.",
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
                "Replace a memory's content by memory_id. This is a full replace, not a patch."
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

    if "list_memories" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "List stored memories, optionally scoped. For finding something "
                "specific, prefer search_memory."
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
            except _InvalidScope as e:
                return e.error
            except ValueError as e:
                return canonical_error("nested_filter", str(e))
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

    if "memory_history" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description="Change history for a single memory by memory_id.",
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
            description="Extracted entities and relationships from stored memories.",
        )
        async def memory_entities(
            scope: dict[str, Any] | None = None,
            limit: int = 100,
        ) -> Any:
            user_id = get_tenant()
            try:
                scope = _validate_scope(scope, allowed_scope_keys)
            except _InvalidScope as e:
                return e.error
            try:
                result = await backend.entities(user_id, scope=scope, limit=limit)
            except ValueError as e:
                return canonical_error("nested_filter", str(e))
            except MemoryAPIError as e:
                return _backend_error(e)
            return {
                "entities": result.entities,
                "relationships": result.relationships,
            }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InvalidScope(Exception):
    def __init__(self, error: dict[str, Any]):
        self.error = error


def _validate_scope(scope: dict[str, Any] | None, allowed_keys: set[str]) -> dict[str, Any] | None:
    """Strip user_id, reject nested filters, validate scope keys."""
    if not scope:
        return scope
    if "user_id" in scope:
        logger.warning("Stripped user_id from scope dict; remaining keys: %s", list(scope.keys()))
        scope = {k: v for k, v in scope.items() if k != "user_id"}
    from memcp.types import reject_nested_filters

    if len(scope) > MAX_SCOPE_KEYS:
        raise _InvalidScope(
            canonical_error("validation_error", f"Scope has too many keys (max {MAX_SCOPE_KEYS})")
        )
    for k, v in scope.items():
        if len(k) > MAX_SCOPE_KEY_LENGTH:
            raise _InvalidScope(
                canonical_error(
                    "validation_error",
                    f"Scope key too long: {k!r} (max {MAX_SCOPE_KEY_LENGTH})",
                )
            )
        if isinstance(v, str) and len(v) > MAX_SCOPE_VALUE_LENGTH:
            raise _InvalidScope(
                canonical_error(
                    "validation_error",
                    f"Scope value too long for {k!r} (max {MAX_SCOPE_VALUE_LENGTH})",
                )
            )
    reject_nested_filters(scope)
    unknown = set(scope) - allowed_keys
    if unknown:
        raise _InvalidScope(
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
