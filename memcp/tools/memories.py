"""Tools that add, find, fetch, change and delete memories: add, search, get, update, delete."""

from __future__ import annotations

from typing import Any

from memcp.policy import Policy
from memcp.tools._shared import DESTRUCTIVE, READ_ONLY
from memcp.types import (
    serialize_memory,
)


def register(mcp: Any, policy: Policy) -> None:
    """Register the add, search, get, update and delete tools."""
    caps = policy.capabilities()

    @mcp.tool(
        description=(
            "Store a fact/preference/decision. Extracts salient facts by "
            "default (may store nothing); infer=false for verbatim. "
            "Bulk: use import_memories."
        )
        if policy.extracts_facts
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
        result = await policy.add(content, scope=scope, metadata=metadata, infer=infer)
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
        results = await policy.search(query, scope=scope, limit=limit, threshold=threshold)
        return {"results": [serialize_memory(m) for m in results]}

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=("Delete one memory by memory_id. Confirm with user first."),
    )
    async def delete_memory(memory_id: str) -> Any:
        return {"deleted": await policy.delete(memory_id)}

    if "get_memory" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description="Fetch a single memory by ID. Returns full content, scope, and metadata.",
        )
        async def get_memory(memory_id: str) -> Any:
            return serialize_memory(await policy.get(memory_id))

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
            return serialize_memory(await policy.update(memory_id, content, metadata=metadata))


def _serialize_add_result(result: Any) -> Any:
    if isinstance(result, list):
        return {
            "results": [
                {"id": r.id, "status": r.status, "created_at": r.created_at} for r in result
            ]
        }
    return {"id": result.id, "status": result.status, "created_at": result.created_at}
