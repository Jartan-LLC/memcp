"""Tools that describe the server and a tenant's memories: status, history, entities."""

from __future__ import annotations

from typing import Any

from memcp.config import Config
from memcp.policy import Policy
from memcp.tools._shared import READ_ONLY


def register(mcp: Any, policy: Policy, config: Config) -> None:
    """Register the status, history and entities tools."""
    caps = policy.capabilities()

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
            "capabilities": sorted(policy.capabilities()),
            "scope_keys": policy.scope_keys(),
            # False means add_memory stores content verbatim and ignores `infer`.
            # The argument shape is the same either way, so this is the only way a
            # caller can find out.
            "extracts_facts": policy.extracts_facts,
            "retrieval": "semantic" if policy.extracts_facts else "keyword",
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
            entries = await policy.history(memory_id)
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
            result = await policy.entities(scope=scope, limit=limit)
            return {
                "entities": result.entities,
                "relationships": result.relationships,
                "incomplete": result.incomplete,
            }
