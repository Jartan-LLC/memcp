"""What memcp refuses to serve, and what the MCP SDK checks on its own.

Two separate things, both recorded here because both were previously unwritten:

- **SEC-2026-0059** — no token *and* a bind another machine can reach is refused at
  startup. memcp resolves tenant identity from the bearer token, so that combination
  is an open memory store, not a permissive one.
- **G8** — the SDK's DNS-rebinding rule, pinned as a test rather than as a claim.
  Thorne could not establish what `host` resolves to with `MEMCP_HOST=0.0.0.0`; the
  answer is that protection is *off*, and these tests fail if that ever changes.
"""

from __future__ import annotations

import json

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from memcp.config import Config, is_loopback
from memcp.server import create_app

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


# --- SEC-2026-0059 ---------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "memory.example.com", ""])
def test_unauthenticated_on_a_reachable_interface_is_refused(host: str):
    with pytest.raises(ValidationError, match="Refusing to start"):
        Config(MEMCP_BACKEND="in_memory", MEMCP_HOST=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
def test_unauthenticated_on_loopback_is_a_dev_server(host: str):
    config = Config(MEMCP_BACKEND="in_memory", MEMCP_HOST=host)
    assert config.memcp_auth_tokens is None


def test_a_token_permits_any_bind():
    config = Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="0.0.0.0", MEMCP_AUTH_TOKENS="t:u")
    assert config.host == "0.0.0.0"


def test_an_empty_token_string_counts_as_no_token():
    with pytest.raises(ValidationError, match="Refusing to start"):
        Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="0.0.0.0", MEMCP_AUTH_TOKENS="")


def test_the_refusal_names_both_ways_out():
    with pytest.raises(ValidationError) as excinfo:
        Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="0.0.0.0")
    message = str(excinfo.value)
    assert "MEMCP_AUTH_TOKENS" in message
    assert "MEMCP_HOST=127.0.0.1" in message


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.5", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("10.0.0.4", False),
        ("example.com", False),
        ("", False),
    ],
)
def test_is_loopback(host: str, expected: bool):
    assert is_loopback(host) is expected


# --- G8: DNS-rebinding protection ------------------------------------------


async def _initialize(config: Config, host_header: str) -> int:
    app, backend = create_app(config)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url=f"http://{host_header}"
        ) as client,
    ):
        resp = await client.post(
            "/mcp",
            json=INIT,
            headers={**MCP_HEADERS, "Authorization": "Bearer tok"},
        )
    await backend.close()
    return resp.status_code


async def test_sdk_leaves_host_validation_off_for_a_non_loopback_bind():
    """mcp 2.0.0: `MEMCP_HOST="0.0.0.0"` means no Host or Origin header is checked at all.

    This is memcp's default bind, so a default deployment validates nothing. That is
    the answer to G8, and the reason MEMCP_ALLOWED_HOSTS exists.
    """
    config = Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="0.0.0.0", MEMCP_AUTH_TOKENS="tok:u")
    assert config.allowed_hosts_list is None
    assert await _initialize(config, "anything.example.com") == 200


async def test_sdk_turns_host_validation_on_for_a_loopback_bind():
    config = Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="127.0.0.1", MEMCP_AUTH_TOKENS="tok:u")
    assert await _initialize(config, "127.0.0.1:8080") == 200
    assert await _initialize(config, "evil.example.com") == 421


async def test_an_explicit_allow_list_admits_only_what_it_names():
    config = Config(
        MEMCP_BACKEND="in_memory",
        MEMCP_HOST="0.0.0.0",
        MEMCP_AUTH_TOKENS="tok:u",
        MEMCP_ALLOWED_HOSTS="memory.example.com,127.0.0.1:*",
    )
    assert config.allowed_hosts_list == ["memory.example.com", "127.0.0.1:*"]
    assert await _initialize(config, "memory.example.com") == 200
    assert await _initialize(config, "127.0.0.1:9999") == 200
    assert await _initialize(config, "evil.example.com") == 421


async def test_a_rejected_host_never_reaches_a_tool():
    """The gate is in front of dispatch, not beside it."""
    config = Config(
        MEMCP_BACKEND="in_memory",
        MEMCP_HOST="0.0.0.0",
        MEMCP_AUTH_TOKENS="tok:u",
        MEMCP_ALLOWED_HOSTS="memory.example.com",
    )
    app, backend = create_app(config)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://evil.example.com"
        ) as client,
    ):
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "add_memory", "arguments": {"content": "leak", "infer": False}},
            },
            headers={**MCP_HEADERS, "Authorization": "Bearer tok"},
        )
        assert resp.status_code == 421

        listed = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_memories", "arguments": {}},
            },
            headers={**MCP_HEADERS, "Authorization": "Bearer tok"},
            extensions={},
        )
    await backend.close()
    assert listed.status_code == 421
    assert "leak" not in json.dumps(listed.text)
