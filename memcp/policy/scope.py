"""Scope validation: the rules every tool applies to a caller's scope."""

from __future__ import annotations

import logging
import re
from typing import Any

from memcp.types import (
    MAX_SCOPE_KEY_LENGTH,
    MAX_SCOPE_KEYS,
    MAX_SCOPE_VALUE_LENGTH,
    canonical_error,
    reject_nested_filters,
)

logger = logging.getLogger(__name__)


class ScopeError(Exception):
    def __init__(self, error: dict[str, Any]):
        self.error = error


# Collapses anything outside [a-z0-9] (after lowercasing) to a single hyphen —
# covers whitespace, underscores, and punctuation from live agents' varied naming
# (e.g. "Claude Code", "claude_code") without 4xx-ing a naming-convention mismatch.
_AGENT_ID_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")


def normalize_agent_id(value: str) -> str:
    """Normalize an agent_id scope value to ``^[a-z0-9-]+$`` form."""
    return _AGENT_ID_COLLAPSE_RE.sub("-", value.strip().lower()).strip("-")


def validate_scope(scope: dict[str, Any] | None, allowed_keys: set[str]) -> dict[str, Any] | None:
    """Strip user_id, reject nested filters, validate scope keys, normalize agent_id.

    agent_id is normalized identically here on every path — write (add_memory,
    import_memories) and filter (search_memory, list_memories, delete_all_memories,
    memory_entities) all call this function, so a write under one casing and a
    filter under another still land on the same normalized value.
    """
    if not scope:
        return scope
    if "user_id" in scope:
        scope = {k: v for k, v in scope.items() if k != "user_id"}
        logger.warning("Stripped user_id from scope dict; remaining keys: %s", list(scope.keys()))
    try:
        reject_nested_filters(scope)
    except ValueError as e:
        raise ScopeError(canonical_error("nested_filter", str(e))) from e
    if len(scope) > MAX_SCOPE_KEYS:
        raise ScopeError(
            canonical_error("validation_error", f"Scope has too many keys (max {MAX_SCOPE_KEYS})")
        )
    cleaned: dict[str, Any] = {}
    for k, v in scope.items():
        if len(k) > MAX_SCOPE_KEY_LENGTH:
            raise ScopeError(
                canonical_error(
                    "validation_error",
                    f"Scope key too long: {k!r} (max {MAX_SCOPE_KEY_LENGTH})",
                )
            )
        if v is not None and not isinstance(v, (str, int, float, bool)):
            raise ScopeError(
                canonical_error(
                    "validation_error",
                    f"Scope value for {k!r} must be a string or number, got {type(v).__name__}",
                )
            )
        if isinstance(v, str) and len(v) > MAX_SCOPE_VALUE_LENGTH:
            raise ScopeError(
                canonical_error(
                    "validation_error",
                    f"Scope value too long for {k!r} (max {MAX_SCOPE_VALUE_LENGTH})",
                )
            )
        if k == "agent_id" and isinstance(v, str):
            normalized = normalize_agent_id(v)
            if not normalized:
                raise ScopeError(
                    canonical_error(
                        "validation_error",
                        f"Scope value for 'agent_id' ({v!r}) has no characters left after "
                        "normalization; use letters, digits, or hyphens.",
                    )
                )
            v = normalized
        cleaned[k] = v
    unknown = set(cleaned) - allowed_keys
    if unknown:
        raise ScopeError(
            canonical_error(
                "invalid_scope",
                f"Unknown scope keys: {sorted(unknown)}. Valid keys: {sorted(allowed_keys)}",
            )
        )
    return cleaned
