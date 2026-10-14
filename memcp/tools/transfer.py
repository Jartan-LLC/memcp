"""Export and import tools, over the transfer engine in `memcp.policy.transfer`."""

from __future__ import annotations

from typing import Any

from memcp.backends import MemoryBackend
from memcp.identity import get_principal, get_tenant
from memcp.policy.metadata import author_for
from memcp.policy.scope import ScopeError, validate_scope
from memcp.policy.transfer import ON_CONFLICT_CHOICES, export_payload, import_payload
from memcp.tools._shared import READ_ONLY, backend_error
from memcp.types import (
    MAX_EXPORT,
    MAX_IMPORT,
    MemoryAPIError,
    canonical_error,
)


def register(mcp: Any, backend: MemoryBackend) -> None:
    """Register export and import when the backend can list memories."""
    allowed_scope_keys = set(backend.scope_keys())
    caps = backend.capabilities()

    if "list_memories" in caps:

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
            user_id = get_tenant()
            try:
                payload = await export_payload(backend, user_id, limit=MAX_EXPORT)
            except MemoryAPIError as e:
                return backend_error(e)
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
                "on_conflict: skip (default), overwrite, duplicate. "
                "dedup_incomplete: true means dedup may have missed existing "
                "memories, so duplicates are possible."
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
            if on_conflict not in ON_CONFLICT_CHOICES:
                return canonical_error(
                    "validation_error",
                    "on_conflict must be 'skip', 'overwrite', or 'duplicate'",
                )
            if on_conflict == "overwrite" and "update_memory" not in caps:
                return canonical_error(
                    "not_supported",
                    "on_conflict='overwrite' requires update_memory capability",
                )

            principal = get_principal()

            def validate_entry_scope(scope: Any) -> Any:
                try:
                    return validate_scope(scope, allowed_scope_keys)
                except ScopeError as e:
                    raise ValueError(e.error["error"]["message"]) from e

            try:
                outcome = await import_payload(
                    backend,
                    principal.tenant,
                    memories,
                    on_conflict=on_conflict,
                    scope_validator=validate_entry_scope,
                    dedup_limit=MAX_EXPORT,
                    author=author_for(principal),
                )
            except MemoryAPIError as e:
                # Only the dedup index read raises out here; per-entry failures are
                # collected into outcome.errors.
                return backend_error(e)

            return {
                "imported": len(outcome.imported),
                "skipped": len(outcome.skipped),
                "errors": outcome.errors,
                "results": outcome.imported,
                "skipped_details": outcome.skipped,
                "dedup_incomplete": outcome.dedup_incomplete,
            }
