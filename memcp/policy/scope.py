"""Scope validation: the rules policy applies to a caller-supplied scope."""

from __future__ import annotations

import re
from typing import Any

from memcp.types import (
    MAX_SCOPE_KEY_LENGTH,
    MAX_SCOPE_KEYS,
    MAX_SCOPE_VALUE_LENGTH,
    PolicyError,
    is_wildcard,
    reject_nested_filters,
)

# Collapses anything outside [a-z0-9] (after lowercasing) to a single hyphen —
# covers whitespace, underscores, and punctuation from live agents' varied naming
# (e.g. "Claude Code", "claude_code") without 4xx-ing a naming-convention mismatch.
_AGENT_ID_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_agent_id(value: str) -> str:
    """Normalize an agent_id scope value to ``^[a-z0-9-]+$`` form."""
    return _AGENT_ID_COLLAPSE_RE.sub("-", value.strip().lower()).strip("-")


def validate_scope(scope: dict[str, Any] | None, allowed_keys: set[str]) -> dict[str, Any] | None:
    """Validate a caller's scope and normalize agent_id; raise PolicyError on any refusal.

    agent_id is normalized the same way on write and filter paths, so a write
    under one casing and a filter under another land on the same value. A value
    that would mean "any" to some backend, or that some backend would trim, is
    refused on every operation, so a scope means the same thing on every backend.
    """
    if scope is None:
        return None
    if not isinstance(scope, dict):
        raise PolicyError("validation_error", "Scope must be an object of keys and values")
    if not scope:
        return scope
    if "user_id" in scope:
        raise PolicyError(
            "invalid_scope", "Scope can't set user_id; the server sets it from your token."
        )
    try:
        reject_nested_filters(scope)
    except ValueError as e:
        raise PolicyError("nested_filter", str(e)) from e
    if len(scope) > MAX_SCOPE_KEYS:
        raise PolicyError("validation_error", f"Scope has too many keys (max {MAX_SCOPE_KEYS})")
    cleaned: dict[str, Any] = {}
    for k, v in scope.items():
        if len(k) > MAX_SCOPE_KEY_LENGTH:
            raise PolicyError(
                "validation_error", f"Scope key too long: {k!r} (max {MAX_SCOPE_KEY_LENGTH})"
            )
        if v is not None and not isinstance(v, (str, int, float, bool)):
            raise PolicyError(
                "validation_error",
                f"Scope value for {k!r} must be a string or number, got {type(v).__name__}",
            )
        if is_wildcard(v):
            raise PolicyError(
                "validation_error",
                f"Scope value for {k!r} must be a concrete value, not empty, '*' or null",
            )
        if isinstance(v, str) and len(v) > MAX_SCOPE_VALUE_LENGTH:
            raise PolicyError(
                "validation_error",
                f"Scope value too long for {k!r} (max {MAX_SCOPE_VALUE_LENGTH})",
            )
        if k == "agent_id" and isinstance(v, str):
            normalized = _normalize_agent_id(v)
            if not normalized:
                raise PolicyError(
                    "validation_error",
                    f"Scope value for 'agent_id' ({v!r}) has no characters left after "
                    "normalization; use letters, digits, or hyphens.",
                )
            v = normalized
        elif isinstance(v, str):
            # mem0 trims both ends of a value and refuses whitespace inside a run_id.
            if v != v.strip():
                raise PolicyError(
                    "validation_error", f"Scope value for {k!r} has whitespace at either end"
                )
            if k == "run_id" and any(c.isspace() for c in v):
                raise PolicyError(
                    "validation_error", "Scope value for 'run_id' must not contain whitespace"
                )
        cleaned[k] = v
    unknown = set(cleaned) - allowed_keys
    if unknown:
        raise PolicyError(
            "invalid_scope",
            f"Unknown scope keys: {sorted(unknown)}. Valid keys: {sorted(allowed_keys)}",
        )
    return cleaned
