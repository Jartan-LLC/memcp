"""Authentication — principal resolution, context propagation, ASGI middleware.

Identity flows: Bearer token → Resolver → ContextVar → tool handlers.

A `Principal` splits *tenant* (what scopes storage — unchanged, SEC-2026-0094
conjunct 2 kept the shared store) from *seat* (who the caller is — the subject
server-side author attribution stamps onto every write). `get_tenant()` keeps
returning a bare string so every existing scoping call site is untouched;
`get_principal()` is additive.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from memcp.types import canonical_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Principal + tenant context (per-request via contextvars)
# ---------------------------------------------------------------------------

_DEFAULT_USER = "default_user"

_SEAT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Principal:
    """A resolved caller: the tenant it scopes storage under, and the seat that
    identifies it for attribution."""

    tenant: str
    seat: str


_DEFAULT_PRINCIPAL = Principal(tenant=_DEFAULT_USER, seat=_DEFAULT_USER)

_principal_var: ContextVar[Principal] = ContextVar("principal")


def get_tenant() -> str:
    """Read the current request's user_id. Falls back to default in dev mode."""
    return _principal_var.get(_DEFAULT_PRINCIPAL).tenant


def get_principal() -> Principal:
    """Read the current request's resolved principal. Falls back to default in dev mode."""
    return _principal_var.get(_DEFAULT_PRINCIPAL)


def is_unattributed(principal: Principal) -> bool:
    """True when `principal` is the dev-mode fallback — nothing was actually
    resolved, so a write made under it should not be stamped as attributed
    (Corin, JAR-723 finding 2).

    Identity, not equality: a real resolved `Principal` that happens to equal
    `_DEFAULT_PRINCIPAL` by value (a token mapped onto memcp's own default
    tenant name, e.g. while migrating a no-auth deployment onto tokens) must
    still count as attributed — that mapping is a legitimate configuration,
    and a value comparison would misread it as the fallback (Corin, JAR-723
    finding 3). Only the literal sentinel object, never rebuilt, ever
    satisfies this — `set_principal(_DEFAULT_PRINCIPAL)` is how a caller opts
    into it deliberately. `Principal` stays exactly `{tenant, seat}` per item
    1 and item 6's own check, so this cannot be a third field."""
    return principal is _DEFAULT_PRINCIPAL


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


# ---------------------------------------------------------------------------
# Resolver protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Resolver(Protocol):
    async def resolve(self, token: str) -> Principal | None:
        """Map a bearer token to a Principal. Returns None for invalid tokens (never raises)."""
        ...


class StaticResolver:
    """Resolves tokens from a static dict (parsed from env var)."""

    def __init__(self, mapping: dict[str, Principal]):
        self._mapping = mapping

    async def resolve(self, token: str) -> Principal | None:
        # Iterate all tokens without early return — constant-time to prevent timing oracle
        matched: Principal | None = None
        for known_token, principal in self._mapping.items():
            if hmac.compare_digest(token.encode(), known_token.encode()):
                matched = principal
        return matched

    @classmethod
    def from_env(cls, raw: str, seats_raw: str | None = None) -> StaticResolver:
        """Parse `MEMCP_AUTH_TOKENS` ('token:user_id', comma-separated) exactly
        as pre-patch: `split(":", 1)` per pair, so a `user_id` containing a
        colon round-trips byte-for-byte unchanged — no seat field is ever read
        out of this string, so there is nothing here for a legacy `user_id` to
        collide with (Corin, JAR-723, correction A re-verification: a bare
        positional third field is genuinely indistinguishable from a
        colon-containing `user_id`, and no fix should have tried to guess
        between them from the string alone).

        `seats_raw` (`MEMCP_AUTH_SEATS`, 'token:seat', comma-separated) is a
        second, independent variable: an optional seat for a token already
        named in `MEMCP_AUTH_TOKENS`, keyed by token rather than embedded in
        the same field. The discriminator is which variable a value came
        from, not its shape. A token in `MEMCP_AUTH_SEATS` with no
        `MEMCP_AUTH_TOKENS` entry is unresolvable and fails closed, named; a
        seat outside `[A-Za-z0-9_.-]+` does too. A token absent from
        `MEMCP_AUTH_SEATS` keeps its seat mirroring its tenant, as it always
        has.
        """
        mapping: dict[str, str] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                raise ValueError(
                    f"Invalid token mapping: {pair!r}. Expected format: token:user_id"
                )
            token, user_id = pair.split(":", 1)
            token, user_id = token.strip(), user_id.strip()
            if not token or not user_id:
                raise ValueError(f"Empty token or user_id in mapping: {pair!r}")
            mapping[token] = user_id
        if not mapping:
            raise ValueError("MEMCP_AUTH_TOKENS is set but contains no valid mappings")

        seats: dict[str, str] = {}
        for pair in (seats_raw or "").split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                raise ValueError(f"Invalid seat mapping: {pair!r}. Expected format: token:seat")
            token, seat = pair.split(":", 1)
            token, seat = token.strip(), seat.strip()
            if not token or not seat:
                raise ValueError(f"Empty token or seat in MEMCP_AUTH_SEATS mapping: {pair!r}")
            if not _SEAT_RE.match(seat):
                raise ValueError(
                    f"Invalid seat label in MEMCP_AUTH_SEATS for token {token!r}: {seat!r}. "
                    f"Seat must match {_SEAT_RE.pattern!r}."
                )
            if token not in mapping:
                raise ValueError(
                    f"MEMCP_AUTH_SEATS names token {token!r}, which has no "
                    "MEMCP_AUTH_TOKENS mapping."
                )
            seats[token] = seat

        return cls(
            {
                token: Principal(tenant=user_id, seat=seats.get(token, user_id))
                for token, user_id in mapping.items()
            }
        )


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------


class BearerGate:
    """ASGI middleware that resolves bearer tokens to tenant identity.

    Raw ASGI (not BaseHTTPMiddleware) to avoid buffering MCP streaming.
    Non-HTTP scopes (lifespan) pass through.
    """

    def __init__(self, app: Any, resolver: Resolver | None):
        self.app = app
        self.resolver = resolver

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.resolver:
            # Dev mode: no auth, default user. The literal sentinel, not a
            # freshly-built equal Principal — is_unattributed() checks identity.
            token = set_principal(_DEFAULT_PRINCIPAL)
            try:
                await self.app(scope, receive, send)
            finally:
                reset_tenant(token)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("utf-8", errors="replace")

        if not provided.startswith("Bearer "):
            logger.warning("Rejected request: missing Bearer prefix (path=%s)", scope.get("path"))
            await self._send_401(send)
            return

        bearer_token = provided[7:]
        principal = await self.resolver.resolve(bearer_token)

        if principal is None:
            logger.warning("Rejected request: invalid token (path=%s)", scope.get("path"))
            await self._send_401(send)
            return

        token = set_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_tenant(token)

    @staticmethod
    async def _send_401(send: Any) -> None:
        err = canonical_error("unauthorized", "Invalid or missing token")
        body = json.dumps(err).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
