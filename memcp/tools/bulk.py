"""Tools over many memories at once: list and scoped delete."""

from __future__ import annotations

from typing import Any

from memcp.backends import MemoryBackend
from memcp.identity import get_tenant
from memcp.policy.scope import ScopeError, validate_scope
from memcp.tools._shared import DESTRUCTIVE, READ_ONLY, backend_error
from memcp.types import (
    MemoryAPIError,
    canonical_error,
    is_wildcard,
    serialize_memory,
    validate_limit,
)


def register(mcp: Any, backend: MemoryBackend) -> None:
    """Register the bulk tools."""
    allowed_scope_keys = set(backend.scope_keys())
    caps = backend.capabilities()

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Bulk-delete memories matching a scope (e.g. agent_id, run_id). "
            "Deletes by scope structure, not content. Requires at least one scope "
            "key, each with a concrete value. Confirm with user first."
        ),
    )
    async def delete_all_memories(scope: dict[str, Any]) -> Any:
        user_id = get_tenant()
        try:
            cleaned = validate_scope(scope, allowed_scope_keys)
        except ScopeError as e:
            return e.error
        if not cleaned:
            return canonical_error(
                "scope_required",
                "delete_all_memories requires at least one scope key.",
            )
        # An empty, "*" or null value means "any" to mem0's adapter; refuse it here so
        # no backend widens a scoped delete.
        if any(is_wildcard(v) for v in cleaned.values()):
            return canonical_error(
                "validation_error",
                "delete_all_memories needs a concrete value for each scope key, "
                "not empty, '*' or null.",
            )
        try:
            count = await backend.delete_all(user_id, cleaned)
        except MemoryAPIError as e:
            return backend_error(e)
        return {"deleted_count": count}

    if "list_memories" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Browse memories, optionally filtered by scope. Unranked, "
                "paginated. incomplete: true, on any page, means memories may "
                "exist that no page reaches. Semantic queries: use search_memory."
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
                scope = validate_scope(scope, allowed_scope_keys)
            except ScopeError as e:
                return e.error
            try:
                result = await backend.list_memories(
                    user_id, scope=scope, limit=limit, cursor=cursor
                )
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            except MemoryAPIError as e:
                return backend_error(e)
            return {
                "memories": [serialize_memory(m) for m in result.memories],
                "next_cursor": result.next_cursor,
                "incomplete": result.incomplete,
            }
