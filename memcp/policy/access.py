"""What a key may do: the relation each operation needs, and the spaces keys may be granted."""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable

from memcp.backends.base import MemoryBackend
from memcp.identity import Key, Relation, current_key
from memcp.policy.space import refuse_unusable_partition
from memcp.types import PolicyError

logger = logging.getLogger(__name__)

# Longer space names may not fit every store's identifier column or field.
PORTABLE_SPACE_LENGTH = 128

FORBIDDEN_MSG = "This key can read its space but not change it; that needs the writer relation."


def require(relation: Relation) -> Key:
    """The current key, when it holds `relation`; a writer also holds reader.

    Raises LookupError when no key is set, and `forbidden` for a reader that needs
    writer. Called before any argument is checked, so a refused key learns nothing
    about the argument rules.
    """
    key = current_key()
    if relation == "writer" and key.grant.relation != "writer":
        raise PolicyError("forbidden", FORBIDDEN_MSG)
    return key


def _not_portable(space: str) -> str | None:
    if any(c.isspace() or unicodedata.category(c) == "Cc" for c in space):
        return "it holds whitespace or control characters"
    if len(space) > PORTABLE_SPACE_LENGTH:
        return f"it is longer than {PORTABLE_SPACE_LENGTH} characters"
    if space.startswith(("spc_", "__")):
        return "it starts with a prefix memcp or a store may reserve (spc_ or __)"
    return None


def check_granted_spaces(backend: MemoryBackend, spaces: Iterable[str]) -> None:
    """Raise ValueError for a granted space no key may use; warn about one that isn't
    portable to every backend, and serve it."""
    for space in dict.fromkeys(spaces):
        try:
            refuse_unusable_partition(backend, space)
        except ValueError as e:
            raise ValueError(f"granted space {space!r} can't be used: {e}") from None
        reason = _not_portable(space)
        if reason:
            logger.warning(
                "Granted space %r may not be portable to every backend: %s", space, reason
            )
