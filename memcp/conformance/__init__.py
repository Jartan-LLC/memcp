"""Backend conformance suite — the gate every MemoryBackend implementation passes.

Run it with `python -m memcp.conformance`. Point it at backends with
MEMCP_CONFORMANCE_BACKENDS and at out-of-tree adapters with
MEMCP_CONFORMANCE_EXTRA; see `docs/conformance.md`.
"""

from __future__ import annotations

from memcp.conformance.capabilities import (
    ALL_CAPABILITIES,
    OPTIONAL_CAPABILITIES,
    REQUIRED_METHODS,
    discover_optional_methods,
)
from memcp.conformance.registry import BackendSpec, all_specs, selected_specs

__all__ = [
    "ALL_CAPABILITIES",
    "OPTIONAL_CAPABILITIES",
    "REQUIRED_METHODS",
    "BackendSpec",
    "all_specs",
    "discover_optional_methods",
    "selected_specs",
]
