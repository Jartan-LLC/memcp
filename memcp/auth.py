"""Authentication — tenant resolution, context propagation, ASGI middleware.

Tenant identity flows: Bearer token → Resolver → ContextVar → tool handlers.
"""

from __future__ import annotations

import hmac
import json
import logging
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from memcp.types import canonical_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tenant context (per-request via contextvars)
# ---------------------------------------------------------------------------

_tenant_var: ContextVar[str] = ContextVar("tenant_user_id")

_DEFAULT_USER = "default_user"


def get_tenant() -> str:
    """Read the current request's user_id. Falls back to default in dev mode."""
    return _tenant_var.get(_DEFAULT_USER)


def set_tenant(user_id: str) -> Any:
    """Set the current request's user_id. Returns a reset token."""
    return _tenant_var.set(user_id)


def reset_tenant(token: Any) -> None:
    """Reset the contextvar to its previous value."""
    _tenant_var.reset(token)


# ---------------------------------------------------------------------------
# Resolver protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Resolver(Protocol):
    async def resolve(self, token: str) -> str | None:
        """Map a bearer token to a user_id. Returns None if invalid."""
        ...


class StaticResolver:
    """Resolves tokens from a static dict (parsed from env var)."""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    async def resolve(self, token: str) -> str | None:
        for known_token, user_id in self._mapping.items():
            if hmac.compare_digest(token, known_token):
                return user_id
        return None

    @classmethod
    def from_env(cls, raw: str) -> StaticResolver:
        """Parse 'token1:user1,token2:user2' format."""
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
        provided = headers.get(b"authorization", b"").decode()

        if not provided.startswith("Bearer "):
            await self._send_401(send)
            return

        bearer_token = provided[7:]
        user_id = await self.resolver.resolve(bearer_token)

        if user_id is None:
            await self._send_401(send)
            return

        token = set_tenant(user_id)
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
        await send({"type": "http.response.body", "body": body})
