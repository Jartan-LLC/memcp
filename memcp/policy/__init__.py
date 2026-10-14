"""The policy layer: every decision about what memcp accepts and returns.

`Policy` is the only caller of a backend. Each method validates its arguments,
calls the backend, and raises `PolicyError` for a request it refuses; a backend
failure it doesn't decide on reaches the caller as `MemoryAPIError`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from memcp.backends import MemoryBackend
from memcp.identity import get_principal, get_tenant
from memcp.policy.metadata import author_for, strip_reserved
from memcp.policy.scope import validate_scope
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


def _in_scope(memories: list[Memory], scope: dict[str, Any] | None) -> list[Memory]:
    """Drop, with a warning, any memory a backend returned outside the requested scope."""
    if not scope:
        return memories
    kept: list[Memory] = []
    for memory in memories:
        if all(str(memory.scope.get(k)) == str(v) for k, v in scope.items()):
            kept.append(memory)
        else:
            logger.warning("Dropped memory %s returned outside the requested scope", memory.id)
    return kept


class Policy:
    """The one object that stands between the tools and a backend."""

    def __init__(self, backend: MemoryBackend):
        self._backend = backend
        self._capabilities = backend.capabilities()
        self._scope_key_list = backend.scope_keys()
        self._scope_keys = set(self._scope_key_list)

    @property
    def extracts_facts(self) -> bool:
        return self._backend.extracts_facts

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
        """Store content for the caller, stamped with its author."""
        with _validation():
            validate_content(content)
        principal = get_principal()
        scope = validate_scope(scope, self._scope_keys)
        metadata = strip_reserved(metadata)
        return await self._backend.add(
            principal.tenant,
            content,
            scope=scope,
            metadata=metadata,
            infer=infer,
            author=author_for(principal),
        )

    async def search(
        self,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]:
        """The caller's memories that match `query`, ranked by the backend."""
        with _validation():
            validate_query(query)
            validate_limit(limit)
            if not (0.0 <= threshold <= 1.0):
                raise ValueError("threshold must be between 0.0 and 1.0")
        user_id = get_tenant()
        scope = validate_scope(scope, self._scope_keys)
        found = await self._backend.search(
            user_id, query, scope=scope, limit=limit, threshold=threshold
        )
        return _in_scope(found, scope)

    async def get(self, memory_id: str) -> Memory:
        """One of the caller's memories; `not_found` when missing or someone else's."""
        user_id = get_tenant()
        with _validation():
            validate_memory_id(memory_id)
        with _not_found():
            result = await self._backend.get(user_id, memory_id)
        if result is None:
            raise PolicyError("not_found", NOT_FOUND_MSG)
        return result

    async def update(
        self, memory_id: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> Memory:
        """Replace a memory's content, re-stamped with the caller as author; `not_found` when
        missing or someone else's."""
        principal = get_principal()
        with _validation():
            validate_memory_id(memory_id)
            validate_content(content)
        metadata = strip_reserved(metadata)
        with _not_found():
            return await self._backend.update(
                principal.tenant,
                memory_id,
                content,
                metadata=metadata,
                author=author_for(principal),
            )

    async def delete(self, memory_id: str) -> bool:
        """Delete one of the caller's memories; `not_found` when missing or someone else's."""
        user_id = get_tenant()
        with _validation():
            validate_memory_id(memory_id)
        with _not_found():
            return await self._backend.delete(user_id, memory_id)

    async def delete_all(self, scope: dict[str, Any]) -> int | None:
        """Delete the caller's memories in a non-empty, concrete scope; the count if known."""
        user_id = get_tenant()
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
        with _validation():
            validate_limit(limit)
        user_id = get_tenant()
        scope = validate_scope(scope, self._scope_keys)
        # A backend raises ValueError for a cursor it can't read.
        with _validation():
            page = await self._backend.list_memories(
                user_id, scope=scope, limit=limit, cursor=cursor
            )
        page.memories = _in_scope(page.memories, scope)
        return page

    async def history(self, memory_id: str) -> list[HistoryEntry]:
        """A memory's change log; `not_found` when missing or someone else's."""
        user_id = get_tenant()
        with _validation():
            validate_memory_id(memory_id)
        with _not_found():
            return await self._backend.history(user_id, memory_id)

    async def entities(
        self, *, scope: dict[str, Any] | None = None, limit: int = 100
    ) -> EntitiesResult:
        """Entity counts over the caller's memories."""
        with _validation():
            validate_limit(limit)
        user_id = get_tenant()
        scope = validate_scope(scope, self._scope_keys)
        return await self._backend.entities(user_id, scope=scope, limit=limit)

    async def export_memories(self) -> ExportPayload:
        """Every memory of the caller's, up to the export cap."""
        return await export_payload(self._backend, get_tenant(), limit=MAX_EXPORT)

    async def import_memories(
        self, memories: Sequence[dict[str, Any]], *, on_conflict: str = "skip"
    ) -> ImportOutcome:
        """Store a batch for the caller; per-entry failures are in the outcome, not raised."""
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
        if on_conflict == "overwrite" and "update_memory" not in self._capabilities:
            raise PolicyError(
                "not_supported", "on_conflict='overwrite' requires update_memory capability"
            )

        principal = get_principal()

        def validate_entry_scope(scope: Any) -> Any:
            try:
                return validate_scope(scope, self._scope_keys)
            except PolicyError as e:
                raise ValueError(e.message) from e

        return await import_payload(
            self._backend,
            principal.tenant,
            memories,
            on_conflict=on_conflict,
            scope_validator=validate_entry_scope,
            dedup_limit=MAX_EXPORT,
            author=author_for(principal),
        )

    async def health(self) -> HealthStatus:
        return await self._backend.health()

    async def close(self) -> None:
        await self._backend.close()
