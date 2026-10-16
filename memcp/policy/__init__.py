"""The policy layer: every decision about what memcp accepts and returns.

`Policy` is the only caller of a backend. Each method checks the current key's relation,
validates its arguments, calls the backend in the key's space, and raises `PolicyError`
for a request it refuses; a backend failure it doesn't decide on reaches the caller as
`MemoryAPIError`.
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
        key = require("writer")
        partition = key.grant.space
        with _validation():
            validate_content(content)
            if metadata is not None:
                validate_metadata(metadata, nulls=False)
                check_metadata_size(metadata)
        scope = validate_scope(scope, self._scope_keys)
        result = await self._backend.add(
            partition,
            content,
            scope=scope,
            metadata=metadata,
            infer=infer,
            system=system_for(partition, key.name, key.name),
        )
        for item in result if isinstance(result, list) else [result]:
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
        user_id = require("reader").grant.space
        with _validation():
            validate_query(query)
            validate_limit(limit)
            if not (0.0 <= threshold <= 1.0):
                raise ValueError("threshold must be between 0.0 and 1.0")
        scope = validate_scope(scope, self._scope_keys)
        found = await self._backend.search(
            user_id, query, scope=scope, limit=limit, threshold=threshold
        )
        return _in_scope([check_space(m, user_id, "search", m.id) for m in found], scope)

    async def get(self, memory_id: str) -> Memory:
        """One of the caller's memories; `not_found` when missing or someone else's."""
        return await self._read(require("reader").grant.space, memory_id, "get")

    async def _read(self, user_id: str, memory_id: str, operation: str) -> Memory:
        with _validation():
            validate_memory_id(memory_id)
        with _not_found():
            result = await self._backend.get(user_id, memory_id)
        if result is None:
            raise PolicyError("not_found", NOT_FOUND_MSG)
        return check_space(result, user_id, operation, memory_id)

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
        key = require("writer")
        partition = key.grant.space
        with _validation():
            if content is None and not metadata:
                raise ValueError("update_memory needs content, or metadata to change")
            if content is not None:
                validate_content(content)
            if metadata:
                validate_metadata(metadata, nulls=True)
        existing = await self._read(partition, memory_id, "update")
        patch = as_patch(metadata) if metadata else None
        if patch is not None:
            # Checked against the read above, not inside the write, so two concurrent
            # patches can together pass the cap: it bounds what one write adds.
            with _validation():
                check_metadata_size(patched(existing.metadata, patch), stored=existing.metadata)
        system = system_for(partition, key.name, existing.system.get("created_by"))
        with _not_found():
            updated = await self._backend.update(
                partition, memory_id, content, patch=patch, system=system
            )
        return check_space(updated, partition, "update", memory_id)

    async def delete(self, memory_id: str) -> bool:
        """Delete one of the caller's memories; `not_found` when missing or someone else's."""
        user_id = require("writer").grant.space
        await self._read(user_id, memory_id, "delete")
        with _not_found():
            return await self._backend.delete(user_id, memory_id)

    async def delete_all(self, scope: dict[str, Any]) -> int | None:
        """Delete the caller's memories in a non-empty, concrete scope; the count if known."""
        user_id = require("writer").grant.space
        cleaned = validate_scope(scope, self._scope_keys)
        if not cleaned:
            raise PolicyError(
                "scope_required", "delete_all_memories requires at least one scope key."
            )
        return await self._backend.delete_all(user_id, cleaned)

    async def list_memories(
        self,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        """One page of the caller's memories."""
        user_id = require("reader").grant.space
        with _validation():
            validate_limit(limit)
        scope = validate_scope(scope, self._scope_keys)
        # A backend raises ValueError for a cursor it can't read.
        with _validation():
            page = await self._backend.list_memories(
                user_id, scope=scope, limit=limit, cursor=cursor
            )
        page.memories = _in_scope(
            [check_space(m, user_id, "list", m.id) for m in page.memories], scope
        )
        return page

    async def history(self, memory_id: str) -> list[HistoryEntry]:
        """A memory's change log; `not_found` when missing or someone else's."""
        user_id = require("reader").grant.space
        await self._read(user_id, memory_id, "history")
        with _not_found():
            return await self._backend.history(user_id, memory_id)

    async def entities(
        self, *, scope: dict[str, Any] | None = None, limit: int = 100
    ) -> EntitiesResult:
        """Entity counts over the caller's memories."""
        user_id = require("reader").grant.space
        with _validation():
            validate_limit(limit)
        scope = validate_scope(scope, self._scope_keys)
        return await self._backend.entities(user_id, scope=scope, limit=limit)

    async def export_memories(self) -> ExportPayload:
        """Every memory of the caller's, up to the export cap."""
        partition = require("reader").grant.space
        return await export_payload(self._backend, partition, limit=MAX_EXPORT)

    async def import_memories(
        self, memories: Sequence[dict[str, Any]], *, on_conflict: str = "skip"
    ) -> ImportOutcome:
        """Store a batch for the caller; per-entry failures are in the outcome, not raised."""
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
                "not_supported", "on_conflict='overwrite' requires the backend's update capability"
            )

        def stamp(index: int, stored: System | None) -> System:
            created_by = key.name if stored is None else stored.get("created_by")
            return system_for(partition, key.name, created_by)

        def validate_entry_scope(scope: Any) -> Any:
            try:
                return validate_scope(scope, self._scope_keys)
            except PolicyError as e:
                raise ValueError(e.message) from e

        return await import_payload(
            self._backend,
            partition,
            memories,
            on_conflict=on_conflict,
            scope_validator=validate_entry_scope,
            dedup_limit=MAX_EXPORT,
            stamp=stamp,
        )

    async def health(self) -> HealthStatus:
        return await self._backend.health()

    async def close(self) -> None:
        await self._backend.close()
