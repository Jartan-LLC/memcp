"""Tools over many memories at once: list and scoped delete."""

from __future__ import annotations

from typing import Any

from memcp.policy import Policy
from memcp.tools._shared import DESTRUCTIVE, READ_ONLY
from memcp.types import serialize_memory


def register(mcp: Any, policy: Policy) -> None:
    """Register the bulk tools."""
    caps = policy.capabilities()

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Bulk-delete memories matching a scope (e.g. agent_id, run_id). "
            "Deletes by scope structure, not content. Requires at least one scope "
            "key, each with a concrete value. Confirm with user first."
        ),
    )
    async def delete_all_memories(scope: dict[str, Any]) -> Any:
        return {"deleted_count": await policy.delete_all(scope)}

    if "list" in caps:

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
            result = await policy.list_memories(scope=scope, limit=limit, cursor=cursor)
            return {
                "memories": [serialize_memory(m) for m in result.memories],
                "next_cursor": result.next_cursor,
                "incomplete": result.incomplete,
            }
