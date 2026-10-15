"""MemoryBackend, the contract every backend implements. @experimental."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from memcp.types import (
    RESERVED_METADATA_PREFIX,
    AddResult,
    EntitiesResult,
    HealthStatus,
    HistoryEntry,
    ListResult,
    Memory,
    MemoryAPIError,
    System,
)

Retrieval = Literal["keyword", "semantic"]
RETRIEVAL_KINDS: tuple[Retrieval, ...] = get_args(Retrieval)

MetadataValue = str | int | float | bool | list[str]

# Keys a store gives its own meaning to (mem0 acts on `expiration_date`, for one), so a
# client can't set or remove them on any backend; so is any key under the prefix.
RESERVED_METADATA_KEYS = frozenset(
    {
        "actor_id",
        "agent_id",
        "attributed_to",
        "created_at",
        "data",
        "expiration_date",
        "hash",
        "id",
        "memory_type",
        "role",
        "run_id",
        "text_lemmatized",
        "updated_at",
        "user_id",
    }
)


def is_reserved_metadata_key(key: str) -> bool:
    return key.startswith(RESERVED_METADATA_PREFIX) or key in RESERVED_METADATA_KEYS


def refuse_reserved_metadata(keys: Iterable[str]) -> None:
    """Raise MemoryAPIError(400) naming the first reserved key among `keys`."""
    for key in keys:
        if is_reserved_metadata_key(key):
            raise MemoryAPIError(400, f"metadata key {key!r} is reserved")


@dataclass(frozen=True)
class MetadataPatch:
    """A merge patch over a memory's metadata: keys to set, and keys to remove."""

    set: Mapping[str, MetadataValue] = field(default_factory=dict[str, MetadataValue])
    remove: frozenset[str] = frozenset()


class MemoryBackend(ABC):
    """Abstract base class for memory storage backends.

    @experimental — validated against the mem0, sqlite and in_memory backends.
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

    # The most memories list_memories can reach across all its pages, or None when
    # nothing caps it.
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
        system: System | None = None,
    ) -> AddResult | list[AddResult]:
        """Store `system` as given and return it, with the partition, on every read.

        An item reported for a memory the store already held keeps that memory's
        stored `created_by` and `space`. None stores no system fields. Raises
        MemoryAPIError(400) for a reserved metadata key.
        """
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
        content: str | None = None,
        *,
        patch: MetadataPatch | None = None,
        system: System | None = None,
    ) -> Memory:
        """Raises MemoryAPIError(404) if not found or in another partition.

        `content`, when given, replaces the text. `patch` sets and removes metadata
        keys and leaves every other stored key as it is; a removed key never reads
        back, not even as null. Raises MemoryAPIError(400) for a reserved key, set or
        removed. `system`, when given, replaces the stored system fields; None keeps
        them.
        """
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
        "add"/"update"); they are not normalized yet.
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
