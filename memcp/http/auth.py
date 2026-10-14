"""Authentication — bearer token resolution and ASGI middleware.

Identity flows: Bearer token → Resolver → ContextVar (`memcp.identity`) → tool handlers.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

from memcp.identity import DEFAULT_PRINCIPAL, Principal, reset_tenant, set_principal
from memcp.types import canonical_error

logger = logging.getLogger(__name__)

_SEAT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

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
        """Parse `MEMCP_AUTH_TOKENS` ('token:user_id', comma-separated) with
        `split(":", 1)` per pair, so a `user_id` containing a colon round-trips
        byte-for-byte unchanged. No seat field is ever read out of this string:
        a positional third field would be indistinguishable from a
        colon-containing `user_id`.

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
        # Errors name an entry by position, never by content: an entry holds a token,
        # and these messages are logged.
        mapping: dict[str, str] = {}
        for n, pair in enumerate(raw.split(","), 1):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                raise ValueError(
                    f"Invalid token mapping: MEMCP_AUTH_TOKENS entry {n}. "
                    "Expected format: token:user_id"
                )
            token, user_id = pair.split(":", 1)
            token, user_id = token.strip(), user_id.strip()
            if not token or not user_id:
                raise ValueError(f"Empty token or user_id in MEMCP_AUTH_TOKENS entry {n}")
            mapping[token] = user_id
        if not mapping:
            raise ValueError("MEMCP_AUTH_TOKENS is set but contains no valid mappings")

        seats: dict[str, str] = {}
        for n, pair in enumerate((seats_raw or "").split(","), 1):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                raise ValueError(
                    f"Invalid seat mapping: MEMCP_AUTH_SEATS entry {n}. "
                    "Expected format: token:seat"
                )
            token, seat = pair.split(":", 1)
            token, seat = token.strip(), seat.strip()
            if not token or not seat:
                raise ValueError(f"Empty token or seat in MEMCP_AUTH_SEATS entry {n}")
            if not _SEAT_RE.match(seat):
                raise ValueError(
                    f"Invalid seat label in MEMCP_AUTH_SEATS entry {n}. "
                    f"Seat must match {_SEAT_RE.pattern!r}."
                )
            if token not in mapping:
                raise ValueError(
                    f"MEMCP_AUTH_SEATS entry {n} names a token with no MEMCP_AUTH_TOKENS mapping."
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
            token = set_principal(DEFAULT_PRINCIPAL)
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
