"""BearerGate, and keys end to end: a token reaches a tool only as the key it resolves to."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from memcp.backends.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.http.app import create_app
from memcp.http.auth import LOCAL_KEY, BearerGate, load_keys
from memcp.identity import Grant, Key, current_key
from tests.keys import write_keys_file

WRITER_TOKEN = "writer-token-not-a-real-credential"
READER_TOKEN = "reader-token-not-a-real-credential"
WRITER = Key(name="laptop", grant=Grant(space="team", relation="writer"))
READER = Key(name="ci", grant=Grant(space="team", relation="reader"))
BASE_URL = "http://127.0.0.1:8080"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _keys_file(tmp_path: Path) -> Path:
    return write_keys_file(
        tmp_path / "keys.toml",
        ("laptop", WRITER_TOKEN, "team", "writer"),
        ("ci", READER_TOKEN, "team", "reader"),
    )


async def _make_request(app: Any, headers: list[tuple[bytes, bytes]] | None = None):
    """Simulate a minimal ASGI HTTP request and capture the response."""
    response_started: dict[str, Any] = {}
    response_body = b""

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, Any]):
        nonlocal response_body
        if message["type"] == "http.response.start":
            response_started.update(message)
        elif message["type"] == "http.response.body":
            response_body = message.get("body", b"")

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers or []}
    await app(scope, receive, send)
    return response_started.get("status", 0), response_body


def _capturing(captured: list[Key]) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        captured.append(current_key())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return app


# --- BearerGate --------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers", [[], [(b"authorization", b"Bearer anything")]], ids=["no token", "a token"]
)
async def test_dev_mode_serves_every_request_as_the_local_key(headers: Any):
    captured: list[Key] = []
    status, _body = await _make_request(BearerGate(_capturing(captured), None), headers)
    assert status == 200
    assert captured == [LOCAL_KEY]
    assert Key(name="local", grant=Grant(space="default_user", relation="writer")) == LOCAL_KEY


async def test_each_token_is_served_as_its_own_key(tmp_path: Path):
    captured: list[Key] = []
    gate = BearerGate(_capturing(captured), load_keys(str(_keys_file(tmp_path))))
    for token in (WRITER_TOKEN, READER_TOKEN):
        status, _body = await _make_request(gate, [(b"authorization", f"Bearer {token}".encode())])
        assert status == 200
    assert captured == [WRITER, READER]


async def test_the_key_is_reset_after_the_request(tmp_path: Path):
    before = current_key()
    gate = BearerGate(_capturing([]), load_keys(str(_keys_file(tmp_path))))
    await _make_request(gate, [(b"authorization", f"Bearer {WRITER_TOKEN}".encode())])
    assert current_key() == before


@pytest.mark.parametrize(
    "header",
    [
        None,
        b"Bearer wrong-token",
        b"Bearer ",
        b"Bearer",
        WRITER_TOKEN.encode(),
        f"Basic {WRITER_TOKEN}".encode(),
    ],
    ids=["no header", "unknown token", "empty token", "no space", "no prefix", "basic"],
)
async def test_without_a_known_token_the_request_gets_401(
    tmp_path: Path, header: bytes | None, caplog: pytest.LogCaptureFixture
):
    captured: list[Key] = []
    gate = BearerGate(_capturing(captured), load_keys(str(_keys_file(tmp_path))))
    headers = [] if header is None else [(b"authorization", header)]
    with caplog.at_level(logging.WARNING, logger="memcp.http.auth"):
        status, body = await _make_request(gate, headers)
    assert status == 401
    assert json.loads(body)["error"]["code"] == "unauthorized"
    assert captured == []
    assert caplog.records, "a rejected request should be logged"
    assert WRITER_TOKEN not in caplog.text
    assert "wrong-token" not in caplog.text


async def test_lifespan_passes_through(tmp_path: Path):
    called = False

    async def lifespan_app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    await BearerGate(lifespan_app, load_keys(str(_keys_file(tmp_path))))(
        {"type": "lifespan"}, None, None
    )
    assert called


# --- end to end through create_app -------------------------------------------------


def _parse(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


async def _post(client: AsyncClient, token: str | None, name: str, arguments: dict[str, Any]):
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )


async def _call(client: AsyncClient, token: str | None, name: str, arguments: dict[str, Any]):
    resp = await _post(client, token, name, arguments)
    assert resp.status_code == 200, f"{name}: HTTP {resp.status_code} {resp.text[:200]}"
    result = _parse(resp.text)["result"]
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def _keyed_config(tmp_path: Path) -> Config:
    return Config(
        MEMCP_BACKEND="in_memory",
        MEMCP_HOST="0.0.0.0",
        MEMCP_KEYS_FILE=str(_keys_file(tmp_path)),
    )


async def test_a_reader_reads_and_is_forbidden_to_write(tmp_path: Path):
    app, backend = create_app(_keyed_config(tmp_path))
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        added = await _call(
            client,
            WRITER_TOKEN,
            "add_memory",
            {"content": "the deploy runs at noon", "infer": False},
        )
        memory_id = added["results"][0]["id"]

        fetched = await _call(client, READER_TOKEN, "get_memory", {"memory_id": memory_id})
        assert fetched["content"] == "the deploy runs at noon"
        assert fetched["author"] == "laptop"
        found = await _call(client, READER_TOKEN, "search_memory", {"query": "deploy noon"})
        assert [m["id"] for m in found["results"]] == [memory_id]

        for name, arguments in [
            ("add_memory", {"content": "planted", "infer": False}),
            ("update_memory", {"memory_id": memory_id, "content": "changed"}),
            ("delete_memory", {"memory_id": memory_id}),
            ("delete_all_memories", {"scope": {"agent_id": "a"}}),
            ("import_memories", {"memories": [{"content": "planted"}]}),
        ]:
            refused = await _call(client, READER_TOKEN, name, arguments)
            assert refused["error"]["code"] == "forbidden", name

        still = await _call(client, WRITER_TOKEN, "get_memory", {"memory_id": memory_id})
        assert still["content"] == "the deploy runs at noon"
    await backend.close()


@pytest.mark.parametrize("token", [None, "unknown-token"], ids=["no token", "unknown token"])
async def test_with_a_keys_file_a_request_without_a_known_token_reaches_no_tool(
    tmp_path: Path, token: str | None
):
    app, backend = create_app(_keyed_config(tmp_path))
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        resp = await _post(client, token, "add_memory", {"content": "planted", "infer": False})
        assert resp.status_code == 401
        health = await client.get("/health")
        assert health.status_code == 200
        # Inside the lifespan: shutdown closes the backend, and in_memory empties on close.
        page = await backend.list_memories("team")
        assert page.memories == []
    await backend.close()


async def test_dev_mode_writes_as_the_local_key(config: Config):
    app, backend = create_app(config)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
    ):
        added = await _call(
            client, None, "add_memory", {"content": "a local fact", "infer": False}
        )
        memory_id = added["results"][0]["id"]
        fetched = await _call(client, None, "get_memory", {"memory_id": memory_id})
        stored = await backend.get("default_user", memory_id)
    assert fetched["author"] == "local"
    assert stored is not None
    await backend.close()


def test_a_malformed_keys_file_stops_startup(tmp_path: Path):
    path = tmp_path / "keys.toml"
    path.write_text('[[keys]]\nname = "laptop"\n', encoding="utf-8")
    config = Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="0.0.0.0", MEMCP_KEYS_FILE=str(path))
    with pytest.raises(ValueError, match="MEMCP_KEYS_FILE"):
        create_app(config)


@pytest.mark.parametrize("space", ["*", "refused-here"])
def test_a_granted_space_the_backend_refuses_stops_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, space: str
):
    def refuse(self: InMemoryBackend, partition: str) -> None:
        if partition == "refused-here":
            raise ValueError("refused for this test")

    monkeypatch.setattr(InMemoryBackend, "validate_partition", refuse)
    path = write_keys_file(tmp_path / "keys.toml", ("laptop", WRITER_TOKEN, space, "writer"))
    config = Config(MEMCP_BACKEND="in_memory", MEMCP_HOST="0.0.0.0", MEMCP_KEYS_FILE=str(path))
    with pytest.raises(ValueError, match=f"space {space!r}"):
        create_app(config)


async def _without_the_test_key(fn: Any) -> Any:
    """Run `fn()` in an empty context, as a server is: the autouse fixture's key is a
    test convenience, and a path that read the key outside the gate would pass with it."""
    return await asyncio.create_task(fn(), context=contextvars.Context())


async def test_health_and_dev_mode_tools_need_no_key_beyond_the_gate(config: Config):
    async def serve() -> tuple[int, Any]:
        app, backend = create_app(config)
        async with (
            LifespanManager(app) as manager,
            AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
        ):
            health = await client.get("/health")
            added = await _call(client, None, "add_memory", {"content": "a fact", "infer": False})
            fetched = await _call(
                client, None, "get_memory", {"memory_id": added["results"][0]["id"]}
            )
        await backend.close()
        return health.status_code, fetched

    status, fetched = await _without_the_test_key(serve)
    assert status == 200
    assert fetched["author"] == "local"


async def test_with_a_keys_file_health_needs_no_token_and_a_token_reaches_tools(tmp_path: Path):
    async def serve() -> tuple[int, Any]:
        app, backend = create_app(_keyed_config(tmp_path))
        async with (
            LifespanManager(app) as manager,
            AsyncClient(transport=ASGITransport(app=manager.app), base_url=BASE_URL) as client,
        ):
            health = await client.get("/health")
            found = await _call(client, READER_TOKEN, "search_memory", {"query": "anything"})
        await backend.close()
        return health.status_code, found

    status, found = await _without_the_test_key(serve)
    assert status == 200
    assert found["results"] == []
