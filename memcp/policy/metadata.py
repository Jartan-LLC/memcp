"""Caller metadata and authorship: the reserved-key strip and the author stamp."""

from __future__ import annotations

import logging
from typing import Any

from memcp.identity import Principal, is_unattributed
from memcp.types import strip_reserved_metadata

logger = logging.getLogger(__name__)


def strip_reserved(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip caller-supplied keys in memcp's reserved metadata namespace — the
    author stamp lives there and the caller cannot set it (SEC-2026-0094)."""
    cleaned = strip_reserved_metadata(metadata)
    if cleaned is not None and metadata is not None and len(cleaned) != len(metadata):
        logger.warning(
            "Stripped reserved metadata keys from metadata dict; remaining keys: %s",
            list(cleaned.keys()),
        )
    return cleaned


def author_for(principal: Principal) -> str | None:
    """The seat to stamp, or None when nothing was actually resolved.

    The dev-mode fallback principal (no MEMCP_AUTH_TOKENS) is not an
    attribution — nothing authenticated it — so a write made under it must
    not claim `attributed: true`."""
    return None if is_unattributed(principal) else principal.seat
