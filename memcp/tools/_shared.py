"""What every tool module shares: tool annotations and backend-error mapping."""

from __future__ import annotations

from typing import Any

from memcp.types import MemoryAPIError, canonical_error

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
DESTRUCTIVE = {"destructiveHint": True}


def backend_error(e: MemoryAPIError) -> dict[str, Any]:
    """Map a MemoryAPIError to the appropriate canonical error."""
    if e.status == 408:
        return canonical_error("timeout", str(e), retry=True)
    return canonical_error("backend_error", str(e), retry=e.status >= 500)
