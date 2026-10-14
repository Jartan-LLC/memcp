"""Tools that describe the server and a tenant's memories: status, history, entities."""

from __future__ import annotations

from typing import Any

from memcp.backends import MemoryBackend
from memcp.config import Config
from memcp.identity import get_tenant
from memcp.policy.scope import ScopeError, validate_scope
from memcp.tools._shared import READ_ONLY, backend_error
from memcp.types import (
    NOT_FOUND_MSG,
    MemoryAPIError,
    canonical_error,
    validate_limit,
    validate_memory_id,
)


def register(mcp: Any, backend: MemoryBackend, config: Config) -> None:
    """Register the status, history and entities tools."""
    allowed_scope_keys = set(backend.scope_keys())
    caps = backend.capabilities()

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
            # False means add_memory stores content verbatim and ignores `infer`.
            # The argument shape is the same either way, so this is the only way a
            # caller can find out.
            "extracts_facts": backend.extracts_facts,
            "retrieval": "semantic" if backend.extracts_facts else "keyword",
        }

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
                return backend_error(e)
            return {
                "history": [
                    {
                        "action": e.action,
                        "timestamp": e.timestamp,
                        "content_before": e.content_before,
                        "content_after": e.content_after,
                        "author": e.author,
                    }
                    for e in entries
                ]
            }

    if "memory_entities" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Knowledge graph: entities and relationships from memories. "
                "incomplete: true means the counts may miss some memories. "
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
                scope = validate_scope(scope, allowed_scope_keys)
            except ScopeError as e:
                return e.error
            try:
                result = await backend.entities(user_id, scope=scope, limit=limit)
            except MemoryAPIError as e:
                return backend_error(e)
            return {
                "entities": result.entities,
                "relationships": result.relationships,
                "incomplete": result.incomplete,
            }
