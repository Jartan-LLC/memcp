"""The current request's identity, propagated through a context variable.

A `Principal` splits *tenant* (what scopes storage) from *seat* (who the caller
is; author attribution stamps it onto every write). `get_tenant()` returns the
bare tenant string; `get_principal()` returns both.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

DEFAULT_USER = "default_user"


@dataclass(frozen=True)
class Principal:
    """A resolved caller: the tenant it scopes storage under, and the seat that
    identifies it for attribution."""

    tenant: str
    seat: str


DEFAULT_PRINCIPAL = Principal(tenant=DEFAULT_USER, seat=DEFAULT_USER)

_principal_var: ContextVar[Principal] = ContextVar("principal")


def get_tenant() -> str:
    """Read the current request's user_id. Falls back to default in dev mode."""
    return _principal_var.get(DEFAULT_PRINCIPAL).tenant


def get_principal() -> Principal:
    """Read the current request's resolved principal. Falls back to default in dev mode."""
    return _principal_var.get(DEFAULT_PRINCIPAL)


def is_unattributed(principal: Principal) -> bool:
    """True when `principal` is the dev-mode fallback — nothing was actually
    resolved, so a write made under it should not be stamped as attributed.

    Identity, not equality: a real resolved `Principal` that happens to equal
    `DEFAULT_PRINCIPAL` by value (a token mapped onto memcp's own default
    tenant name, e.g. while migrating a no-auth deployment onto tokens) must
    still count as attributed — that mapping is a legitimate configuration,
    and a value comparison would misread it as the fallback. Only the literal
    sentinel object, never rebuilt, ever satisfies this —
    `set_principal(DEFAULT_PRINCIPAL)` is how a caller opts into it
    deliberately."""
    return principal is DEFAULT_PRINCIPAL


def set_tenant(user_id: str) -> Any:
    """Set the current request's principal from a bare tenant id (seat mirrors
    tenant). Returns a reset token."""
    return _principal_var.set(Principal(tenant=user_id, seat=user_id))


def set_principal(principal: Principal) -> Any:
    """Set the current request's principal. Returns a reset token."""
    return _principal_var.set(principal)


def reset_tenant(token: Any) -> None:
    """Reset the contextvar to its previous value."""
    _principal_var.reset(token)
