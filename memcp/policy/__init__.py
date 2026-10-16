"""The policy layer: every decision about what memcp accepts and returns.

`Policy` is the only caller of a backend. Each data method checks the current key's
relation, validates its arguments, calls the backend in the key's space, and raises
`PolicyError` for a request it refuses; a backend failure it doesn't decide on reaches the
caller as `MemoryAPIError`. `health` and `close` need no key.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from memcp.backends import MemoryBackend
from memcp.backends.base import RETRIEVAL_KINDS, Retrieval
from memcp.backends.capabilities import ALL_CAPABILITIES
from memcp.policy.access import require
from memcp.policy.audit import AuditEntry, audited
from memcp.policy.metadata import (
    as_patch,
    check_metadata_size,
    patched,
    system_for,
    validate_metadata,
)
from memcp.policy.scope import validate_scope
from memcp.policy.space import check_space
from memcp.policy.transfer import (
    ON_CONFLICT_CHOICES,
    ExportPayload,
    ImportOutcome,
    export_payload,
    import_payload,
)
from memcp.types import (
    MAX_EXPORT,
    MAX_IMPORT,
    NOT_FOUND_MSG,
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
    MemoryAPIError,
    PolicyError,
    System,
    scope_text,
    validate_content,
    validate_limit,
    validate_memory_id,
    validate_query,
)

logger = logging.getLogger(__name__)


@contextmanager
def _validation() -> Iterator[None]:
    """Refuse a request whose arguments fail a check, as `validation_error`."""
    try:
        yield
    except ValueError as e:
        raise PolicyError("validation_error", str(e)) from e


@contextmanager
def _not_found() -> Iterator[None]:
    """Refuse a by-id request the backend can't find, as `not_found`."""
    try:
        yield
    except MemoryAPIError as e:
        if e.status in (404, 410):
            raise PolicyError("not_found", NOT_FOUND_MSG) from e
        raise


def _matches(stored: Any, requested: Any) -> bool:
    """Whether a returned value is the requested one, compared as stored text."""
    return scope_text(stored) == scope_text(requested)


def _in_scope(memories: list[Memory], scope: dict[str, Any] | None) -> list[Memory]:
    """Drop, with a warning, any memory a backend returned outside the requested scope."""
    if not scope:
        return memories
    kept: list[Memory] = []
    for memory in memories:
        if all(k in memory.scope and _matches(memory.scope[k], v) for k, v in scope.items()):
            kept.append(memory)
        else:
            logger.warning("Dropped memory %s returned outside the requested scope", memory.id)
    return kept


class Policy:
    """The one object that stands between the tools and a backend."""

    def __init__(self, backend: MemoryBackend):
        """Raises TypeError for a backend whose retrieval kind or capabilities are unknown."""
        name = type(backend).__name__
        retrieval = getattr(backend, "retrieval", None)
        if retrieval not in RETRIEVAL_KINDS:
            raise TypeError(
                f"{name}.retrieval must be one of {RETRIEVAL_KINDS}, not {retrieval!r}"
            )
        self._capabilities = backend.capabilities()
        unknown = self._capabilities - set(ALL_CAPABILITIES)
        if unknown:
            raise TypeError(
                f"{name} declares unknown capabilities {sorted(unknown)}; "
                f"the known ones are {ALL_CAPABILITIES}"
            )
        self._backend = backend
        self._scope_key_list = backend.scope_keys()
        self._scope_keys = set(self._scope_key_list)

    @property
    def extracts_facts(self) -> bool:
        return self._backend.extracts_facts

    @property
    def retrieval(self) -> Retrieval:
        return self._backend.retrieval

    def capabilities(self) -> set[str]:
        """The optional operations the backend declares."""
        return set(self._capabilities)

    def scope_keys(self) -> list[str]:
        """The scope keys the backend supports, in its order."""
        return list(self._scope_key_list)

    async def add(
        self,
        content: str,
        *,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> AddResult | list[AddResult]:
        """Store content for the caller, stamped as its write; each item is space-checked."""
        with audited("add") as entry:
            key = require("writer")
            partition = key.grant.space
            with _validation():
                validate_content(content)
                if metadata is not None:
                    validate_metadata(metadata, nulls=False)
                    check_metadata_size(metadata)
            scope = entry.scope = validate_scope(scope, self._scope_keys)
            result = await self._backend.add(
                partition,
                content,
                scope=scope,
                metadata=metadata,
                infer=infer,
                system=system_for(partition, key.name, key.name),
            )
            items = result if isinstance(result, list) else [result]
            entry.ids = [item.id for item in items]
            for item in items:
                stored = await self._backend.get(partition, item.id)
                check_space(stored, partition, "add", item.id)
            return result

    async def search(
        self,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]:
        """The caller's memories that match `query`, ranked by the backend."""
        with audited("search") as entry:
            partition = require("reader").grant.space
            with _validation():
                validate_query(query)
                validate_limit(limit)
                if not (0.0 <= threshold <= 1.0):
                    raise ValueError("threshold must be between 0.0 and 1.0")
            scope = entry.scope = validate_scope(scope, self._scope_keys)
            found = await self._backend.search(
                partition, query, scope=scope, limit=limit, threshold=threshold
            )
            kept = _in_scope([check_space(m, partition, "search", m.id) for m in found], scope)
            entry.count = len(kept)
            return kept

    async def get(self, memory_id: str) -> Memory:
        """One of the caller's memories; `not_found` when missing or someone else's."""
        with audited("get") as entry:
            return await self._read(require("reader").grant.space, memory_id, "get", entry)

    async def _read(
        self, partition: str, memory_id: str, operation: str, entry: AuditEntry
    ) -> Memory:
        with _validation():
            validate_memory_id(memory_id)
        # Recorded only once it is a valid id: a client could send anything here.
        entry.ids = [memory_id]
        with _not_found():
            result = await self._backend.get(partition, memory_id)
        if result is None:
            raise PolicyError("not_found", NOT_FOUND_MSG)
        return check_space(result, partition, operation, memory_id)

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """Change a memory's content, its metadata (a merge patch: null removes a key), or
        both, with the caller as author and its creator kept; `not_found` when missing or
        someone else's."""
        with audited("update") as entry:
            key = require("writer")
            partition = key.grant.space
            with _validation():
                if content is None and not metadata:
                    raise ValueError("update_memory needs content, or metadata to change")
                if content is not None:
                    validate_content(content)
                if metadata:
                    validate_metadata(metadata, nulls=True)
            existing = await self._read(partition, memory_id, "update", entry)
            patch = as_patch(metadata) if metadata else None
            if patch is not None:
                # Checked against the read above, not inside the write, so two concurrent
                # patches can together pass the cap: it bounds what one write adds.
                with _validation():
                    check_metadata_size(
                        patched(existing.metadata, patch), stored=existing.metadata
                    )
            system = system_for(partition, key.name, existing.system.get("created_by"))
            with _not_found():
                updated = await self._backend.update(
                    partition, memory_id, content, patch=patch, system=system
                )
            return check_space(updated, partition, "update", memory_id)

    async def delete(self, memory_id: str) -> bool:
        """Delete one of the caller's memories; `not_found` when missing or someone else's."""
        with audited("delete") as entry:
            partition = require("writer").grant.space
            await self._read(partition, memory_id, "delete", entry)
            with _not_found():
                return await self._backend.delete(partition, memory_id)

    async def delete_all(self, scope: dict[str, Any]) -> int | None:
        """Delete the caller's memories in a non-empty, concrete scope; the count if known."""
        with audited("delete_all") as entry:
            partition = require("writer").grant.space
            cleaned = entry.scope = validate_scope(scope, self._scope_keys)
            if not cleaned:
                raise PolicyError(
                    "scope_required", "delete_all_memories requires at least one scope key."
                )
            entry.count = await self._backend.delete_all(partition, cleaned)
            return entry.count

    async def list_memories(
        self,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        """One page of the caller's memories."""
        with audited("list_memories") as entry:
            partition = require("reader").grant.space
            with _validation():
                validate_limit(limit)
            scope = entry.scope = validate_scope(scope, self._scope_keys)
            # A backend raises ValueError for a cursor it can't read.
            with _validation():
                page = await self._backend.list_memories(
                    partition, scope=scope, limit=limit, cursor=cursor
                )
            page.memories = _in_scope(
                [check_space(m, partition, "list", m.id) for m in page.memories], scope
            )
            entry.count = len(page.memories)
            return page

    async def history(self, memory_id: str) -> list[HistoryEntry]:
        """A memory's change log; `not_found` when missing or someone else's."""
        with audited("history") as entry:
            partition = require("reader").grant.space
            await self._read(partition, memory_id, "history", entry)
            with _not_found():
                return await self._backend.history(partition, memory_id)

    async def entities(
        self, *, scope: dict[str, Any] | None = None, limit: int = 100
    ) -> EntitiesResult:
        """Entity counts over the caller's memories."""
        with audited("entities") as entry:
            partition = require("reader").grant.space
            with _validation():
                validate_limit(limit)
            scope = entry.scope = validate_scope(scope, self._scope_keys)
            result = await self._backend.entities(partition, scope=scope, limit=limit)
            entry.count = len(result.entities)
            return result

    async def export_memories(self) -> ExportPayload:
        """Every memory of the caller's, up to the export cap."""
        with audited("export_memories") as entry:
            partition = require("reader").grant.space
            payload = await export_payload(self._backend, partition, limit=MAX_EXPORT)
            entry.count = payload.count
            return payload

    async def import_memories(
        self, memories: Sequence[dict[str, Any]], *, on_conflict: str = "skip"
    ) -> ImportOutcome:
        """Store a batch for the caller; per-entry failures are in the outcome, not raised."""
        with audited("import_memories") as entry:
            key = require("writer")
            partition = key.grant.space
            if not memories:
                raise PolicyError("validation_error", "memories array must not be empty")
            if len(memories) > MAX_IMPORT:
                raise PolicyError(
                    "validation_error", f"Too many memories to import (limit {MAX_IMPORT})"
                )
            if on_conflict not in ON_CONFLICT_CHOICES:
                raise PolicyError(
                    "validation_error", "on_conflict must be 'skip', 'overwrite', or 'duplicate'"
                )
            if on_conflict == "overwrite" and "update" not in self._capabilities:
                raise PolicyError(
                    "not_supported",
                    "on_conflict='overwrite' requires the backend's update capability",
                )

            def stamp(index: int, stored: System | None) -> System:
                created_by = key.name if stored is None else stored.get("created_by")
                return system_for(partition, key.name, created_by)

            def validate_entry_scope(scope: Any) -> Any:
                try:
                    return validate_scope(scope, self._scope_keys)
                except PolicyError as e:
                    raise ValueError(e.message) from e

            outcome = await import_payload(
                self._backend,
                partition,
                memories,
                on_conflict=on_conflict,
                scope_validator=validate_entry_scope,
                dedup_limit=MAX_EXPORT,
                stamp=stamp,
            )
            entry.count = len(outcome.imported)
            return outcome

    async def health(self) -> HealthStatus:
        return await self._backend.health()

    async def close(self) -> None:
        await self._backend.close()
