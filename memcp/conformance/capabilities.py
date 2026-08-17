"""Capability model shared by the conformance suite and the tool layer.

Capability strings are MCP tool names — that is what `MemoryBackend.capabilities()`
returns and what `register_tools` gates registration on.
"""

from __future__ import annotations

import inspect
from types import MappingProxyType

from memcp.backend.base import MemoryBackend

# Abstract methods every backend must implement. Derived from the ABC so a new
# abstract method cannot slip past the suite.
REQUIRED_METHODS: tuple[str, ...] = tuple(sorted(MemoryBackend.__abstractmethods__))

# Methods that exist on the ABC purely for lifecycle — neither required nor
# capability-gated.
LIFECYCLE_METHODS = frozenset({"close"})

# capability name -> the MemoryBackend method it gates
OPTIONAL_CAPABILITIES = MappingProxyType(
    {
        "get_memory": "get",
        "update_memory": "update",
        "list_memories": "list_memories",
        "memory_history": "history",
        "memory_entities": "entities",
    }
)

ALL_CAPABILITIES: tuple[str, ...] = tuple(sorted(OPTIONAL_CAPABILITIES))


def discover_optional_methods() -> frozenset[str]:
    """Optional methods on MemoryBackend: public, concrete, not lifecycle.

    A5's coverage check compares this against OPTIONAL_CAPABILITIES, so adding an
    optional method to the ABC without giving it a capability fails the suite
    instead of shipping untested.
    """
    abstract = set(MemoryBackend.__abstractmethods__)
    found = set()
    for name, obj in vars(MemoryBackend).items():
        if name.startswith("_") or name in abstract or name in LIFECYCLE_METHODS:
            continue
        if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
            found.add(name)
    return frozenset(found)


def capability_for_method(method: str) -> str | None:
    for cap, meth in OPTIONAL_CAPABILITIES.items():
        if meth == method:
            return cap
    return None
