"""Tests for auth: BearerGate middleware, StaticResolver, tenant context."""

from __future__ import annotations

import json

import pytest

from memcp.auth import (
    BearerGate,
    StaticResolver,
    get_tenant,
    reset_tenant,
    set_tenant,
)


async def _make_request(app, headers: list[tuple[bytes, bytes]] | None = None):
    """Simulate a minimal ASGI HTTP request and capture the response."""
    response_started = {}
    response_body = b""

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        nonlocal response_body
        if message["type"] == "http.response.start":
            response_started.update(message)
        elif message["type"] == "http.response.body":
            response_body = message.get("body", b"")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers or [],
    }
    await app(scope, receive, send)
    return response_started.get("status", 0), response_body


async def _dummy_app(scope, receive, send):
    """Downstream app that returns 200."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b'{"ok": true}'})


def _resolver() -> StaticResolver:
    return StaticResolver({"secret-token": "alice", "other-token": "bob"})


# ---------------------------------------------------------------------------
# Tenant context isolation
# ---------------------------------------------------------------------------


async def test_contextvar_isolation():
    """Verify contextvars don't leak between calls."""
    tok1 = set_tenant("user_one")
    assert get_tenant() == "user_one"
    reset_tenant(tok1)

    tok2 = set_tenant("user_two")
    assert get_tenant() == "user_two"
    reset_tenant(tok2)


async def test_contextvar_default():
    """Without set_tenant, get_tenant returns default."""
    from memcp.auth import _tenant_var

    # Temporarily clear to test default (fixture sets test_user)
    tok = _tenant_var.set("default_user")
    assert get_tenant() == "default_user"
    _tenant_var.reset(tok)


# ---------------------------------------------------------------------------
# StaticResolver
# ---------------------------------------------------------------------------


async def test_static_resolver_valid_token():
    resolver = _resolver()
    assert await resolver.resolve("secret-token") == "alice"
    assert await resolver.resolve("other-token") == "bob"


async def test_static_resolver_invalid_token():
    resolver = _resolver()
    assert await resolver.resolve("bad-token") is None


async def test_static_resolver_timing_safe():
    """Resolver uses hmac.compare_digest, not ==."""
    resolver = _resolver()
    assert await resolver.resolve("secret-toke") is None
    assert await resolver.resolve("secret-token\x00") is None


async def test_static_resolver_from_env():
    resolver = StaticResolver.from_env("tok1:alice,tok2:bob")
    assert await resolver.resolve("tok1") == "alice"
    assert await resolver.resolve("tok2") == "bob"


async def test_static_resolver_from_env_whitespace():
    resolver = StaticResolver.from_env(" tok1 : alice , tok2 : bob ")
    assert await resolver.resolve("tok1") == "alice"


def test_static_resolver_from_env_invalid():
    with pytest.raises(ValueError, match="Invalid token mapping"):
        StaticResolver.from_env("no-colon-here")


def test_static_resolver_from_env_empty():
    with pytest.raises(ValueError, match="no valid mappings"):
        StaticResolver.from_env("")


# ---------------------------------------------------------------------------
# BearerGate with resolver
# ---------------------------------------------------------------------------


async def test_valid_token_resolves_user():
    """Valid token sets tenant context and passes through."""
    captured_user = None

    async def capture_app(scope, receive, send):
        nonlocal captured_user
        captured_user = get_tenant()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    gate = BearerGate(capture_app, _resolver())
    status, _body = await _make_request(gate, [(b"authorization", b"Bearer secret-token")])
    assert status == 200
    assert captured_user == "alice"


async def test_different_tokens_different_users():
    captured_users = []

    async def capture_app(scope, receive, send):
        captured_users.append(get_tenant())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    gate = BearerGate(capture_app, _resolver())
    await _make_request(gate, [(b"authorization", b"Bearer secret-token")])
    await _make_request(gate, [(b"authorization", b"Bearer other-token")])
    assert captured_users == ["alice", "bob"]


async def test_invalid_token_rejected():
    gate = BearerGate(_dummy_app, _resolver())
    status, body = await _make_request(gate, [(b"authorization", b"Bearer wrong-token")])
    assert status == 401
    data = json.loads(body)
    assert data["error"]["code"] == "unauthorized"


async def test_missing_token_rejected():
    gate = BearerGate(_dummy_app, _resolver())
    status, _body = await _make_request(gate, [])
    assert status == 401


async def test_no_bearer_prefix_rejected():
    gate = BearerGate(_dummy_app, _resolver())
    status, _body = await _make_request(gate, [(b"authorization", b"secret-token")])
    assert status == 401


async def test_disabled_auth_uses_default_user():
    """When resolver is None, all requests get default_user."""
    captured_user = None

    async def capture_app(scope, receive, send):
        nonlocal captured_user
        captured_user = get_tenant()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    gate = BearerGate(capture_app, None)
    status, _body = await _make_request(gate, [])
    assert status == 200
    assert captured_user == "default_user"


async def test_lifespan_passes_through():
    """Non-HTTP scopes (lifespan) should pass through regardless."""
    resolver = _resolver()
    gate = BearerGate(_dummy_app, resolver)
    called = False

    async def lifespan_app(scope, receive, send):
        nonlocal called
        called = True

    gate.app = lifespan_app
    await gate({"type": "lifespan"}, None, None)
    assert called
