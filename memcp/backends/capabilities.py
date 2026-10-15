"""Optional backend capabilities.

A capability names an optional `MemoryBackend` operation: what `capabilities()` returns,
and what policy and the tools layer gate on.
"""

from __future__ import annotations

from types import MappingProxyType

# capability name -> the MemoryBackend method it gates
OPTIONAL_CAPABILITIES = MappingProxyType(
    {
        "update": "update",
        "list": "list_memories",
        "history": "history",
        "entities": "entities",
    }
)

ALL_CAPABILITIES: tuple[str, ...] = tuple(sorted(OPTIONAL_CAPABILITIES))
