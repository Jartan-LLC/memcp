"""MemoryBackend ABC — @experimental, will change in v0.2 when second backend is added."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from memcp.types import (
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
)


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

    # --- required (universal tools) ---

    @abstractmethod
    async def add(
        self,
        user_id: str,
        content: str,
        *,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> AddResult | list[AddResult]: ...

    @abstractmethod
    async def search(
        self,
        user_id: str,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]: ...

    @abstractmethod
    async def delete(self, user_id: str, memory_id: str) -> bool:
        """Raises MemoryAPIError(404) if not found or not owned by user_id."""
        ...

    @abstractmethod
    async def delete_all(self, user_id: str, scope: dict[str, Any]) -> int | None:
        """Returns count of deleted memories, or None if backend doesn't report counts."""
        ...

    @abstractmethod
    async def health(self) -> HealthStatus: ...

    @abstractmethod
    def capabilities(self) -> set[str]: ...

    @abstractmethod
    def scope_keys(self) -> list[str]: ...

    # --- optional (declared in capabilities()) ---

    async def get(self, user_id: str, memory_id: str) -> Memory | None:
        """Returns None if not found or not owned by user_id."""
        raise NotImplementedError

    async def update(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """Raises MemoryAPIError(404) if not found or not owned by user_id."""
        raise NotImplementedError

    async def list_memories(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        raise NotImplementedError

    async def history(self, user_id: str, memory_id: str) -> list[HistoryEntry]:
        """Raises MemoryAPIError(404) if not found or not owned by user_id.

        Note: action strings are backend-specific (e.g. "created"/"updated" vs
        "add"/"update"). Normalize in v0.2 when the Protocol stabilizes.
        """
        raise NotImplementedError

    async def entities(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> EntitiesResult:
        raise NotImplementedError

    # --- lifecycle ---

    async def close(self) -> None:  # noqa: B027
        """Clean up resources. Called on shutdown."""
