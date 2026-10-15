"""MemoryBackend ABC — @experimental, will change in v0.2 when second backend is added."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, get_args

from memcp.types import (
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
)

Retrieval = Literal["keyword", "semantic"]
RETRIEVAL_KINDS: tuple[Retrieval, ...] = get_args(Retrieval)


class MemoryBackend(ABC):
    """Abstract base class for memory storage backends.

    @experimental — validated against mem0 and in-memory adapters.
    Will be refined when Cognee adapter is added in v0.2.
    """

    # Whether add(infer=True) actually extracts facts, or stores content verbatim.
    # A backend with no model behind it accepts `infer` and cannot honour it, and a
    # caller has no way to discover that from the argument shape — so this is
    # reported by memory_status and stated in add_memory's description. It is a
    # class attribute rather than a method on purpose: the conformance suite pairs
    # every optional *method* with a capability, and this gates no tool.
    extracts_facts: bool = False

    # Whether search ranks by meaning ("semantic") or by matching words ("keyword").
    # Required, and checked when Policy is built: pyright won't let a class attribute
    # override an abstract property.
    retrieval: Retrieval

    # The most memories one list call can reach, or None when nothing caps it.
    list_ceiling: int | None = None

    # --- required (universal tools) ---

    @abstractmethod
    async def add(
        self,
        partition: str,
        content: str,
        *,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
        author: str | None = None,
    ) -> AddResult | list[AddResult]:
        """`author`, when given, is the resolved seat stamped into the reserved
        metadata namespace (SEC-2026-0094 conjunct 2) — never sourced from the
        caller's `metadata`. None means the caller bypassed attribution (a direct
        backend call, or a request the auth layer never resolved); the memory
        comes back unattributed rather than guessing."""
        ...

    @abstractmethod
    async def search(
        self,
        partition: str,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]: ...

    @abstractmethod
    async def delete(self, partition: str, memory_id: str) -> bool:
        """Raises MemoryAPIError(404) if not found or in another partition."""
        ...

    @abstractmethod
    async def delete_all(self, partition: str, scope: dict[str, Any]) -> int | None:
        """Returns count of deleted memories, or None if backend doesn't report counts."""
        ...

    @abstractmethod
    async def health(self) -> HealthStatus: ...

    @abstractmethod
    def capabilities(self) -> set[str]: ...

    @abstractmethod
    def scope_keys(self) -> list[str]: ...

    @abstractmethod
    async def get(self, partition: str, memory_id: str) -> Memory | None:
        """Returns None if not found or in another partition."""
        ...

    # --- optional (declared in capabilities()) ---

    async def update(
        self,
        partition: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        author: str | None = None,
    ) -> Memory:
        """Raises MemoryAPIError(404) if not found or in another partition.

        `author`, when given, always re-stamps — it is never inherited from the
        memory's prior author, even when `metadata` is omitted. Inheriting it
        would launder attribution: an attacker who edits a trusted seat's memory
        would keep that seat's label (SEC-2026-0094 conjunct 2, item 4)."""
        raise NotImplementedError

    async def list_memories(
        self,
        partition: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        """One page of the tenant's memories, with `next_cursor` set while more remain.

        Set `incomplete` on every page when memories may exist beyond what any page
        reaches (a store's list ceiling). Raises `ValueError` for a cursor it can't read.
        """
        raise NotImplementedError

    async def history(self, partition: str, memory_id: str) -> list[HistoryEntry]:
        """Raises MemoryAPIError(404) if not found or in another partition.

        Note: action strings are backend-specific (e.g. "created"/"updated" vs
        "add"/"update"). Normalize in v0.2 when the Protocol stabilizes.
        """
        raise NotImplementedError

    async def entities(
        self,
        partition: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> EntitiesResult:
        """Set `incomplete` when the counts come from an incomplete `list_memories` result."""
        raise NotImplementedError

    # --- lifecycle ---

    async def close(self) -> None:  # noqa: B027
        """Clean up resources. Called on shutdown."""
