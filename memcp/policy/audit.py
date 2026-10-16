"""The audit log: one record per policy operation, on the logger `memcp.audit`.

A record names the operation, the key (or a migration's operator), its space and
relation, the outcome, and the memory ids or the scope and a count. It never holds
content, a query or metadata.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from memcp.identity import current_key
from memcp.types import MemoryAPIError, PolicyError

logger = logging.getLogger("memcp.audit")


@dataclass
class AuditEntry:
    """What one operation did, filled in by the operation as it learns it."""

    operation: str
    key: str | None
    space: str | None
    relation: str | None
    outcome: str = "ok"
    ids: list[str] | None = None
    count: int | None = None
    scope: dict[str, Any] | None = None


def _outcome(e: BaseException) -> str:
    if isinstance(e, PolicyError):
        return e.code
    if isinstance(e, MemoryAPIError):
        return "timeout" if e.timed_out else "backend_error"
    if isinstance(e, ValueError):
        return "validation_error"
    return "error"


@contextmanager
def audited(
    operation: str, *, operator: str | None = None, space: str | None = None
) -> Iterator[AuditEntry]:
    """Log exactly one record for the operation run inside, whatever its outcome.

    The current key is the actor; `operator` replaces it for a call no key makes
    (`migrate`), with `space` and no relation.
    """
    if operator is not None:
        entry = AuditEntry(operation, key=operator, space=space, relation=None)
    else:
        try:
            key = current_key()
        except LookupError:
            entry = AuditEntry(operation, key=None, space=None, relation=None)
        else:
            entry = AuditEntry(operation, key.name, key.grant.space, key.grant.relation)
    try:
        yield entry
    except BaseException as e:
        entry.outcome = _outcome(e)
        raise
    finally:
        logger.info(
            "audit operation=%s outcome=%s key=%s space=%s relation=%s ids=%s count=%s scope=%s",
            entry.operation,
            entry.outcome,
            entry.key,
            entry.space,
            entry.relation,
            entry.ids,
            entry.count,
            entry.scope,
            extra={"audit": asdict(entry)},
        )
