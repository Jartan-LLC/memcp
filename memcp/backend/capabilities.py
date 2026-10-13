"""Optional backend capabilities.

Capability strings are MCP tool names: what `MemoryBackend.capabilities()` returns and
what `register_tools` gates registration on.
"""

from __future__ import annotations

from types import MappingProxyType

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
