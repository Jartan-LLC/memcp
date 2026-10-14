"""Tools for one memory at a time: add, search, get, update, delete."""

from __future__ import annotations

from typing import Any

from memcp.backends import MemoryBackend
from memcp.identity import get_principal, get_tenant
from memcp.policy.metadata import author_for, strip_reserved
from memcp.policy.scope import ScopeError, validate_scope
from memcp.tools._shared import DESTRUCTIVE, READ_ONLY, backend_error
from memcp.types import (
    NOT_FOUND_MSG,
    MemoryAPIError,
    canonical_error,
    serialize_memory,
    validate_content,
    validate_limit,
    validate_memory_id,
    validate_query,
)


def register(mcp: Any, backend: MemoryBackend) -> None:
    """Register the single-memory tools."""
    allowed_scope_keys = set(backend.scope_keys())
    caps = backend.capabilities()

    @mcp.tool(
        description=(
            "Store a fact/preference/decision. Extracts salient facts by "
            "default (may store nothing); infer=false for verbatim. "
            "Bulk: use import_memories."
        )
        if backend.extracts_facts
        else (
            "Store a fact/preference/decision. This backend has no model behind it, "
            "so content is stored verbatim and the infer argument is accepted but "
            "has no effect. Bulk: use import_memories."
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
        principal = get_principal()
        try:
            scope = validate_scope(scope, allowed_scope_keys)
        except ScopeError as e:
            return e.error
        metadata = strip_reserved(metadata)
        try:
            result = await backend.add(
                principal.tenant,
                content,
                scope=scope,
                metadata=metadata,
                infer=infer,
                author=author_for(principal),
            )
        except MemoryAPIError as e:
            return backend_error(e)

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
            scope = validate_scope(scope, allowed_scope_keys)
        except ScopeError as e:
            return e.error
        try:
            results = await backend.search(
                user_id, query, scope=scope, limit=limit, threshold=threshold
            )
        except MemoryAPIError as e:
            return backend_error(e)

        return {"results": [serialize_memory(m) for m in results]}

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
            return backend_error(e)
        return {"deleted": result}

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
                return backend_error(e)
            if result is None:
                return canonical_error("not_found", NOT_FOUND_MSG)
            return serialize_memory(result)

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
            principal = get_principal()
            try:
                validate_memory_id(memory_id)
                validate_content(content)
            except ValueError as e:
                return canonical_error("validation_error", str(e))
            metadata = strip_reserved(metadata)
            try:
                result = await backend.update(
                    principal.tenant,
                    memory_id,
                    content,
                    metadata=metadata,
                    author=author_for(principal),
                )
            except MemoryAPIError as e:
                if e.status in (404, 410):
                    return canonical_error("not_found", NOT_FOUND_MSG)
                return backend_error(e)
            return serialize_memory(result)


def _serialize_add_result(result: Any) -> Any:
    if isinstance(result, list):
        return {
            "results": [
                {"id": r.id, "status": r.status, "created_at": r.created_at} for r in result
            ]
        }
    return {"id": result.id, "status": result.status, "created_at": result.created_at}
