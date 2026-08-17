"""Export, import and backend-to-backend migration.

The `export_memories` / `import_memories` MCP tools and the conformance round trip
both go through here, so dedup and conflict semantics have exactly one
implementation. Nothing in this module validates MCP arguments or formats MCP
errors — that stays in `memcp.tools`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from memcp.backend.base import MemoryBackend
from memcp.types import (
    MAX_EXPORT,
    Memory,
    MemoryAPIError,
    serialize_memory,
    validate_content,
)

ON_CONFLICT_CHOICES = ("skip", "overwrite", "duplicate")

ScopeKey = tuple[tuple[str, str], ...]
DedupKey = tuple[str, ScopeKey]


def scope_key(scope: Mapping[str, Any] | None) -> ScopeKey:
    """Order-independent, stringified scope identity. None-valued keys are absent.

    Backends report scope differently — the mem0 adapter only reconstructs keys it
    finds set, the in-memory one echoes whatever was stored — so both sides of a
    dedup comparison have to be normalised the same way.
    """
    if not scope:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in scope.items() if v is not None))


def dedup_key(content: str, scope: Mapping[str, Any] | None) -> DedupKey:
    """Identity used to decide whether an imported memory already exists.

    Same content in the same scope is a duplicate; same content in a different
    scope is a distinct memory (GitHub #30).
    """
    return (content, scope_key(scope))


@dataclass
class ExportPayload:
    memories: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    truncated: bool = False


@dataclass
class ImportOutcome:
    imported: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MigrationReport:
    source: str
    target: str
    exported: int = 0
    truncated: bool = False
    imported: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


async def export_payload(
    backend: MemoryBackend,
    user_id: str,
    *,
    limit: int = MAX_EXPORT,
) -> ExportPayload:
    """Read every memory for a tenant, capped at `limit`.

    Requires the `list_memories` capability; raises MemoryAPIError on backend
    failure for the caller to map.
    """
    result = await backend.list_memories(user_id, limit=limit + 1)
    truncated = len(result.memories) > limit
    memories = result.memories[:limit]
    return ExportPayload(
        memories=[serialize_memory(m) for m in memories],
        count=len(memories),
        truncated=truncated,
    )


async def build_dedup_index(
    backend: MemoryBackend,
    user_id: str,
    *,
    limit: int = MAX_EXPORT,
) -> dict[DedupKey, str]:
    """Map (content, scope) -> memory id for a tenant's existing memories.

    Capped at `limit`: tenants above it get best-effort dedup rather than a
    refused import.
    """
    result = await backend.list_memories(user_id, limit=limit + 1)
    return {dedup_key(m.content, m.scope): m.id for m in result.memories}


async def import_payload(
    backend: MemoryBackend,
    user_id: str,
    memories: Sequence[Mapping[str, Any]],
    *,
    on_conflict: str = "skip",
    scope_validator: Callable[[Any], Any] | None = None,
    dedup_limit: int = MAX_EXPORT,
) -> ImportOutcome:
    """Store `memories` verbatim (no extraction), deduped on content plus scope.

    `scope_validator` may rewrite a scope dict or raise ValueError to reject one
    entry; its message lands in `errors` for that index. Raises ValueError for an
    unusable `on_conflict`, and MemoryAPIError if the dedup index cannot be read.
    """
    if on_conflict not in ON_CONFLICT_CHOICES:
        raise ValueError(f"on_conflict must be one of {ON_CONFLICT_CHOICES}")
    if on_conflict == "overwrite" and "update_memory" not in backend.capabilities():
        raise ValueError("on_conflict='overwrite' requires the update_memory capability")

    existing: dict[DedupKey, str] = {}
    if on_conflict != "duplicate":
        existing = await build_dedup_index(backend, user_id, limit=dedup_limit)

    outcome = ImportOutcome()

    for i, entry in enumerate(memories):
        content = entry.get("content")
        if not isinstance(content, str):
            outcome.errors.append({"index": i, "error": "missing or invalid content"})
            continue
        try:
            validate_content(content)
        except ValueError as e:
            outcome.errors.append({"index": i, "error": str(e)})
            continue

        scope = entry.get("scope")
        if scope and scope_validator is not None:
            try:
                scope = scope_validator(scope)
            except ValueError as e:
                outcome.errors.append({"index": i, "error": str(e)})
                continue
        metadata = entry.get("metadata")

        key = dedup_key(content, scope)
        dup_id = existing.get(key)

        if dup_id and on_conflict == "skip":
            outcome.skipped.append({"index": i, "existing_id": dup_id})
            continue

        if dup_id and on_conflict == "overwrite":
            try:
                await backend.update(user_id, dup_id, content, metadata=metadata)
                outcome.imported.append({"id": dup_id, "index": i, "action": "updated"})
            except MemoryAPIError as e:
                outcome.errors.append({"index": i, "error": str(e)})
            continue

        try:
            result = await backend.add(
                user_id, content, scope=scope, metadata=metadata, infer=False
            )
        except MemoryAPIError as e:
            outcome.errors.append({"index": i, "error": str(e)})
            continue

        if not result:
            outcome.errors.append({"index": i, "error": "stored nothing"})
            continue
        items = result if isinstance(result, list) else [result]
        for r in items:
            outcome.imported.append({"id": r.id, "index": i, "action": "created"})
            existing[key] = r.id

    return outcome


async def migrate(
    source: MemoryBackend,
    target: MemoryBackend,
    user_id: str,
    *,
    target_user_id: str | None = None,
    on_conflict: str = "skip",
    limit: int = MAX_EXPORT,
    source_name: str = "source",
    target_name: str = "target",
    scope_validator: Callable[[Any], Any] | None = None,
) -> MigrationReport:
    """Copy one tenant's memories from `source` into `target` (GitHub #27).

    `target_user_id` defaults to `user_id` — pass it to land the memories under a
    different tenant on the target. Both backends need the `list_memories`
    capability. What does not survive the trip is documented per pair in
    `docs/portability.md`; see `memcp.conformance.portability`.
    """
    payload = await export_payload(source, user_id, limit=limit)
    outcome = await import_payload(
        target,
        target_user_id or user_id,
        payload.memories,
        on_conflict=on_conflict,
        scope_validator=scope_validator,
        dedup_limit=limit,
    )
    return MigrationReport(
        source=source_name,
        target=target_name,
        exported=payload.count,
        truncated=payload.truncated,
        imported=len(outcome.imported),
        skipped=len(outcome.skipped),
        errors=outcome.errors,
    )


def memory_by_identity(memories: Sequence[Memory]) -> dict[DedupKey, Memory]:
    """Index memories by (content, scope) — how a migrated memory is found again."""
    return {dedup_key(m.content, m.scope): m for m in memories}
