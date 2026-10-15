"""Caller metadata and authorship: the metadata rules and the system stamp."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from memcp.backends.base import MetadataPatch, is_reserved_metadata_key
from memcp.identity import Principal, is_unattributed
from memcp.types import MAX_METADATA_BYTES, MAX_METADATA_KEY_LENGTH, System


def _is_value(value: Any) -> bool:
    if isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, list) and all(isinstance(v, str) for v in value)  # pyright: ignore[reportUnknownVariableType]


def validate_metadata(metadata: Mapping[str, Any], *, nulls: bool) -> None:
    """Raise ValueError naming the first key that breaks a rule. `nulls` admits a null
    value, which means "remove this key" in an update."""
    for key, value in metadata.items():
        if is_reserved_metadata_key(key):
            raise ValueError(f"metadata key {key!r} is reserved")
        if value is None and nulls:
            # A removal: a key stored before the length rule existed can still go.
            continue
        if len(key) > MAX_METADATA_KEY_LENGTH:
            raise ValueError(
                f"metadata key {key!r} is longer than {MAX_METADATA_KEY_LENGTH} characters"
            )
        if value is None:
            raise ValueError(f"metadata key {key!r} is null; only update_memory takes null")
        if not _is_value(value):
            raise ValueError(
                f"metadata key {key!r} must be a string, a finite number, a boolean "
                "or a list of strings"
            )


def _size(metadata: Mapping[str, Any]) -> int:
    return len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode())


def check_metadata_size(
    metadata: Mapping[str, Any], *, stored: Mapping[str, Any] | None = None
) -> None:
    """Raise ValueError when `metadata`, once stored, would be over the size cap.

    With `stored`, a write that doesn't grow the metadata is accepted even over the cap, so
    metadata stored before the cap existed can still be shrunk.
    """
    size = _size(metadata)
    if size > MAX_METADATA_BYTES and (stored is None or size > _size(stored)):
        raise ValueError(
            f"metadata would be {size} bytes as JSON; "
            f"the limit is {MAX_METADATA_BYTES // 1024} KiB"
        )


def as_patch(metadata: Mapping[str, Any]) -> MetadataPatch:
    """update_memory's argument as a patch: a null value removes its key."""
    return MetadataPatch(
        set={k: v for k, v in metadata.items() if v is not None},
        remove=frozenset(k for k, v in metadata.items() if v is None),
    )


def patched(stored: Mapping[str, Any], patch: MetadataPatch) -> dict[str, Any]:
    """`stored` with `patch` applied: what a backend will hold after the update."""
    result = {k: v for k, v in stored.items() if k not in patch.remove}
    result.update(patch.set)
    return result


def author_for(principal: Principal) -> str | None:
    """The seat to stamp, or None when nothing was actually resolved.

    The dev-mode fallback principal (no MEMCP_AUTH_TOKENS) is not an
    attribution — nothing authenticated it — so a write made under it records
    no author."""
    return None if is_unattributed(principal) else principal.seat


def system_for(partition: str, author: str | None, created_by: str | None) -> System:
    """The system fields to write, leaving out `author` and `created_by` when they are None."""
    system = System(space=partition)
    if author is not None:
        system["author"] = author
    if created_by is not None:
        system["created_by"] = created_by
    return system
