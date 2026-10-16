"""Export, import and backend-to-backend migration.

`Policy.export_memories` / `Policy.import_memories` and the conformance round trip
both go through here, so dedup and conflict semantics have exactly one
implementation. Argument validation stays in `Policy`, and error formatting in
`memcp.tools._shared.translate_errors`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from memcp.backends.base import MemoryBackend, MetadataPatch, is_reserved_metadata_key
from memcp.policy.audit import audited, can_record
from memcp.policy.metadata import check_metadata_size, validate_metadata
from memcp.policy.space import check_space, refuse_unusable_partition
from memcp.types import (
    MAX_EXPORT,
    Memory,
    MemoryAPIError,
    System,
    log_backend_error,
    scope_text,
    serialize_memory,
    validate_content,
)

logger = logging.getLogger(__name__)

ON_CONFLICT_CHOICES = ("skip", "overwrite", "duplicate")

ScopeKey = tuple[tuple[str, str], ...]
DedupKey = tuple[str, ScopeKey]
# The system fields to write for import entry `index`, given the stored system of the
# memory it overwrites, or None when it creates one.
Stamp = Callable[[int, System | None], System]


def scope_key(scope: Mapping[str, Any] | None) -> ScopeKey:
    """Order-independent scope identity, values as stored text. None-valued keys are absent.

    Backends return scope values as text, and an import entry carries them as JSON
    values, so both sides of a dedup comparison go through `scope_text`.
    """
    if not scope:
        return ()
    return tuple(sorted((str(k), scope_text(v)) for k, v in scope.items() if v is not None))


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
    dedup_incomplete: bool = False


@dataclass
class MigrationReport:
    source: str
    target: str
    exported: int = 0
    truncated: bool = False
    imported: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    dedup_incomplete: bool = False


async def _list_all(
    backend: MemoryBackend, user_id: str, limit: int, operation: str
) -> tuple[list[Memory], bool]:
    """Read up to `limit` space-checked memories by following `next_cursor`, and whether
    any were missed.

    Missed means more than `limit` exist, a page was marked incomplete, or the backend
    returned an empty page with a cursor still set.
    """
    memories: list[Memory] = []
    incomplete = False
    cursor: str | None = None
    while True:
        page = await backend.list_memories(user_id, limit=limit + 1 - len(memories), cursor=cursor)
        memories.extend(check_space(m, user_id, operation, m.id) for m in page.memories)
        incomplete = incomplete or page.incomplete
        cursor = page.next_cursor
        if cursor is None or not page.memories or len(memories) > limit:
            break
    # A cursor left over means the read stopped early: at the cap, or on an empty page.
    return memories[:limit], incomplete or len(memories) > limit or cursor is not None


async def export_payload(
    backend: MemoryBackend,
    user_id: str,
    *,
    limit: int = MAX_EXPORT,
) -> ExportPayload:
    """Read every memory for a tenant, capped at `limit`.

    `truncated` is True when the read missed memories (see `_list_all`). Requires the
    `list` capability; raises MemoryAPIError on backend failure for the caller
    to map.
    """
    memories, truncated = await _list_all(backend, user_id, limit, "export")
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
    operation: str = "import",
) -> tuple[dict[DedupKey, Memory], bool]:
    """Map (content, scope) -> memory for a tenant's existing memories.

    Capped at `limit`: tenants above it get best-effort dedup rather than a
    refused import. Also returns whether the index missed memories.
    """
    memories, incomplete = await _list_all(backend, user_id, limit, operation)
    return {dedup_key(m.content, m.scope): m for m in memories}, incomplete


async def import_payload(
    backend: MemoryBackend,
    user_id: str,
    memories: Sequence[Mapping[str, Any]],
    *,
    on_conflict: str = "skip",
    scope_validator: Callable[[Any], Any] | None = None,
    dedup_limit: int = MAX_EXPORT,
    stamp: Stamp | None = None,
    operation: str = "import",
) -> ImportOutcome:
    """Store `memories` verbatim (no extraction), deduped on content plus scope.

    `scope_validator` may rewrite a scope dict or raise ValueError to reject one
    entry; its message lands in `errors` for that index. Raises ValueError for an
    unusable `on_conflict`, MemoryAPIError if the dedup index cannot be read, and
    PolicyError if a memory read or written fails the space check.

    `stamp` gives each created or overwritten memory its system fields; without it a
    created memory carries none and an overwritten one keeps its own. `operation`
    names the caller in a space-check log. Only an entry's `content`, `scope` and
    `metadata` are read, so an exported `author` is never imported back.
    """
    if on_conflict not in ON_CONFLICT_CHOICES:
        raise ValueError(f"on_conflict must be one of {ON_CONFLICT_CHOICES}")
    if on_conflict == "overwrite" and "update" not in backend.capabilities():
        raise ValueError("on_conflict='overwrite' requires the backend's update capability")

    existing: dict[DedupKey, Memory] = {}
    outcome = ImportOutcome()
    backend_failures = 0

    def record_backend_failure(index: int, e: MemoryAPIError) -> None:
        """Tell the caller the fixed message; log only the first failure's response."""
        nonlocal backend_failures
        if not backend_failures:
            log_backend_error(e, f"import entry {index}")
        backend_failures += 1
        outcome.errors.append({"index": index, "error": e.client_message})

    if on_conflict != "duplicate":
        existing, outcome.dedup_incomplete = await build_dedup_index(
            backend, user_id, limit=dedup_limit, operation=operation
        )

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
        if scope is not None and scope_validator is not None:
            try:
                scope = scope_validator(scope)
            except ValueError as e:
                outcome.errors.append({"index": i, "error": str(e)})
                continue
        raw_metadata = entry.get("metadata")
        if raw_metadata is not None and not isinstance(raw_metadata, dict):
            outcome.errors.append({"index": i, "error": "metadata must be an object"})
            continue
        metadata: dict[str, Any] | None = raw_metadata
        try:
            if metadata is not None:
                validate_metadata(metadata, nulls=False)
            check_metadata_size(metadata or {})
        except ValueError as e:
            outcome.errors.append({"index": i, "error": str(e)})
            continue

        key = dedup_key(content, scope)
        dup = existing.get(key)

        if dup and on_conflict == "skip":
            outcome.skipped.append({"index": i, "existing_id": dup.id})
            continue

        if dup and on_conflict == "overwrite":
            # The memory ends with the entry's metadata: its keys set, the others removed,
            # except a reserved key, which no client can remove. An entry without
            # `metadata` leaves the stored metadata as it is.
            patch = None
            if metadata is not None:
                patch = MetadataPatch(
                    set=metadata,
                    remove=frozenset(
                        k
                        for k in dup.metadata
                        if k not in metadata and not is_reserved_metadata_key(k)
                    ),
                )
            system = stamp(i, dup.system) if stamp else None
            try:
                updated = await backend.update(
                    user_id, dup.id, content, patch=patch, system=system
                )
            except MemoryAPIError as e:
                record_backend_failure(i, e)
                continue
            existing[key] = check_space(updated, user_id, operation, dup.id)
            outcome.imported.append({"id": dup.id, "index": i, "action": "updated"})
            continue

        try:
            result = await backend.add(
                user_id,
                content,
                scope=scope,
                metadata=metadata,
                infer=False,
                system=stamp(i, None) if stamp else None,
            )
        except MemoryAPIError as e:
            record_backend_failure(i, e)
            continue

        if not result:
            outcome.errors.append({"index": i, "error": "stored nothing"})
            continue
        items = result if isinstance(result, list) else [result]
        for r in items:
            try:
                stored = await backend.get(user_id, r.id)
            except MemoryAPIError as e:
                record_backend_failure(i, e)
                continue
            existing[key] = check_space(stored, user_id, operation, r.id)
            outcome.imported.append({"id": r.id, "index": i, "action": "created"})

    if backend_failures > 1:
        logger.warning(
            "Backend errors on import: %d more after the first, responses not logged",
            backend_failures - 1,
        )
    return outcome


async def migrate(
    source: MemoryBackend,
    target: MemoryBackend,
    partition: str,
    *,
    operator: str,
    target_partition: str | None = None,
    on_conflict: str = "skip",
    limit: int = MAX_EXPORT,
    source_name: str = "source",
    target_name: str = "target",
    scope_validator: Callable[[Any], Any] | None = None,
) -> MigrationReport:
    """Copy one partition's memories from `source` into `target` (GitHub #27).

    `target_partition` defaults to `partition`; pass it to land the memories under a
    different partition on the target. `operator` names who runs the migration; the audit
    record gives it as the key. Raises ValueError, before reading anything, for a blank
    operator, logging that would drop the audit record, an empty or `*` partition, or one
    its backend refuses. Both backends need the `list` capability. What does not survive
    the trip is listed in `docs/reference.md`
    ("Moving memories between backends"), except `author`: each copy keeps the source
    memory's system fields, with `space` set to the target partition.
    """
    if not operator.strip():
        raise ValueError("migrate needs an operator: the name of who runs it")
    if not can_record():
        # migrate runs from operator code, which may never have configured logging.
        raise ValueError(
            "migrate writes an audit record to the logger memcp.audit at INFO, and no handler "
            "would write it (the logger is disabled, below INFO, or has no handler at INFO): "
            "configure logging first, for example with memcp.http.logging.setup_logging()"
        )
    landing = partition if target_partition is None else target_partition
    with audited("migrate", operator=operator, space=landing, source_space=partition) as entry:
        try:
            refuse_unusable_partition(source, partition)
            refuse_unusable_partition(target, landing)
        except ValueError:
            entry.outcome = "validation_error"
            raise
        memories, truncated = await _list_all(source, partition, limit, "migrate")

        def copy_system(index: int, stored: System | None) -> System:
            system = System(**memories[index].system)
            system["space"] = landing
            return system

        outcome = await import_payload(
            target,
            landing,
            [serialize_memory(m) for m in memories],
            on_conflict=on_conflict,
            scope_validator=scope_validator,
            dedup_limit=limit,
            stamp=copy_system,
            operation="migrate",
        )
        entry.count = len(outcome.imported)
        return MigrationReport(
            source=source_name,
            target=target_name,
            exported=len(memories),
            truncated=truncated,
            imported=len(outcome.imported),
            skipped=len(outcome.skipped),
            errors=outcome.errors,
            dedup_incomplete=outcome.dedup_incomplete,
        )


def memory_by_identity(memories: Sequence[Memory]) -> dict[DedupKey, Memory]:
    """Index memories by (content, scope) — how a migrated memory is found again."""
    return {dedup_key(m.content, m.scope): m for m in memories}
