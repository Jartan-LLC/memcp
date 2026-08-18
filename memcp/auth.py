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
    def from_env(cls, raw: str) -> StaticResolver:
        """Parse 'token:user_id' or 'token:user_id:seat' pairs, comma-separated.

        The two forms are told apart by field count after a full split on ':' —
        never by widening a maxsplit — because a `user_id` may itself contain a
        colon under the two-field form. A pair that splits into anything other
        than 2 or 3 fields is ambiguous and fails closed rather than being
        silently truncated: a `user_id` (or `user_id:seat`) that happens to
        contain a colon must be rejected at startup, not misparsed. Two fields
        means no seat was given, so seat mirrors user_id (attribution is at
        least as specific as tenancy already was). Three fields means an
        explicit seat, constrained to `[A-Za-z0-9_.-]+`.
        """
        mapping: dict[str, Principal] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            fields = [f.strip() for f in pair.split(":")]
            if len(fields) == 2:
                token, user_id = fields
                seat = user_id
            elif len(fields) == 3:
                token, user_id, seat = fields
                if not _SEAT_RE.match(seat):
                    raise ValueError(
                        f"Invalid seat label in mapping: {pair!r}. Seat must match "
                        f"{_SEAT_RE.pattern!r}."
                    )
            else:
                raise ValueError(
                    f"Invalid token mapping: {pair!r}. Expected 'token:user_id' or "
                    "'token:user_id:seat' — got an ambiguous field count "
                    f"({len(fields)}). A user_id or seat containing ':' is not "
                    "supported."
                )
            if not token or not user_id or not seat:
                raise ValueError(f"Empty token, user_id or seat in mapping: {pair!r}")
            mapping[token] = Principal(tenant=user_id, seat=seat)
        if not mapping:
            raise ValueError("MEMCP_AUTH_TOKENS is set but contains no valid mappings")
        return cls(mapping)


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
            # Dev mode: no auth, default user
            token = set_tenant(_DEFAULT_USER)
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
