"""Export and import tools."""

from __future__ import annotations

from typing import Any

from memcp.policy import Policy
from memcp.tools._shared import READ_ONLY


def register(mcp: Any, policy: Policy) -> None:
    """Register export and import when the backend can list memories."""
    caps = policy.capabilities()

    if "list" in caps:

        @mcp.tool(
            annotations=READ_ONLY,
            description=(
                "Export all memories as JSON. For backup/migration — output "
                "compatible with import_memories. truncated: true means some "
                "memories may have been left out. Browsing: list_memories; "
                "search: search_memory."
            ),
        )
        async def export_memories() -> Any:
            payload = await policy.export_memories()
            return {
                "memories": payload.memories,
                "count": payload.count,
                "truncated": payload.truncated,
            }

        @mcp.tool(
            description=(
                "Batch-import from JSON array. Each entry needs 'content'; "
                "optional 'scope'/'metadata'. Stored verbatim (no extraction). "
                "Deduped by content within the same scope — identical content in a "
                "different scope is a distinct memory. "
                "on_conflict: skip (default), overwrite, duplicate. overwrite replaces "
                "the memory's metadata when the entry has 'metadata', and keeps it "
                "otherwise. "
                "dedup_incomplete: true means dedup may have missed existing "
                "memories, so duplicates are possible."
            ),
        )
        async def import_memories(
            memories: list[dict[str, Any]],
            on_conflict: str = "skip",
        ) -> Any:
            outcome = await policy.import_memories(memories, on_conflict=on_conflict)
            return {
                "imported": len(outcome.imported),
                "skipped": len(outcome.skipped),
                "errors": outcome.errors,
                "results": outcome.imported,
                "skipped_details": outcome.skipped,
                "dedup_incomplete": outcome.dedup_incomplete,
            }
