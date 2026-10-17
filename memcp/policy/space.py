"""The space check: every memory policy reads back is in the partition it asked for, and
no space uses a partition a store can't keep apart."""

from __future__ import annotations

import logging

from memcp.backends.base import MemoryBackend
from memcp.types import Memory, PolicyError, is_wildcard

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MSG = "The request failed an internal check."


def check_space(memory: Memory | None, partition: str, operation: str, memory_id: str) -> Memory:
    """`memory` when its partition, and its `system["space"]` if set, are `partition`.

    Anything else fails the request: a memory from another space must never reach the
    caller, and a backend that returns one can't be trusted for the rest of the call.
    """
    if (
        memory is not None
        and memory.partition == partition
        and memory.system.get("space", partition) == partition
    ):
        return memory
    logger.error(
        "isolation_violation: operation=%s memory=%s partition=%s stored_partition=%s "
        "stored_space=%s",
        operation,
        memory_id,
        partition,
        None if memory is None else memory.partition,
        None if memory is None else memory.system.get("space"),
    )
    raise PolicyError("internal_error", INTERNAL_ERROR_MSG)


def refuse_unusable_partition(backend: MemoryBackend, partition: str) -> None:
    """Raise ValueError for a partition no space may use: empty, `*`, or one `backend` can't
    match exactly."""
    if is_wildcard(partition):
        raise ValueError(f"partition {partition!r} is empty or a wildcard")
    backend.validate_partition(partition)
