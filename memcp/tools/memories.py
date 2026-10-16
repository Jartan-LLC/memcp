"""Tools that add, find, fetch, change and delete memories: add, search, get, update, delete."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import TypeAdapter, WithJsonSchema

from memcp.backends.base import MetadataValue
from memcp.policy import Policy
from memcp.tools._shared import DESTRUCTIVE, READ_ONLY
from memcp.types import serialize_memory

# The schemas advertise the allowed values but the SDK doesn't enforce them, so a bad
# value reaches policy, which refuses it with an error naming the key.
Metadata = Annotated[
    dict[str, Any] | None,
    WithJsonSchema(TypeAdapter(dict[str, MetadataValue] | None).json_schema()),
]
MetadataChanges = Annotated[
    dict[str, Any] | None,
    WithJsonSchema(TypeAdapter(dict[str, MetadataValue | None] | None).json_schema()),
]


def register(mcp: Any, policy: Policy) -> None:
    """Register the add, search, get, update and delete tools."""
    caps = policy.capabilities()

    @mcp.tool(
        description=(
            "Store a fact/preference/decision. Extracts salient facts by "
            "default (may store nothing); infer=false for verbatim. Each result's "
            "event says whether extraction added, updated or deleted that memory. "
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
        metadata: Metadata = None,
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

    @mcp.tool(
        annotations=READ_ONLY,
        description="Fetch a single memory by ID. Returns full content, scope, and metadata.",
    )
    async def get_memory(memory_id: str) -> Any:
        return serialize_memory(await policy.get(memory_id))

    if "update" in caps:

        @mcp.tool(
            annotations={"idempotentHint": True, "destructiveHint": True},
            description=(
                "Update a memory by memory_id. Give `content`, `metadata` or both. "
                "`content` replaces its text. `metadata` is a patch: keys you list are "
                "set, a key set to null is removed, keys you leave out are kept. Returns "
                "the updated memory. Scope is immutable; to change it, add a new memory "
                "and delete the old one."
            ),
        )
        async def update_memory(
            memory_id: str,
            content: str | None = None,
            metadata: MetadataChanges = None,
        ) -> Any:
            return serialize_memory(await policy.update(memory_id, content, metadata=metadata))


def _serialize_add_result(result: Any) -> Any:
    if isinstance(result, list):
        return {
            "results": [
                {"id": r.id, "status": r.status, "created_at": r.created_at, "event": r.event}
                for r in result
            ]
        }
    return {
        "id": result.id,
        "status": result.status,
        "created_at": result.created_at,
        "event": result.event,
    }
