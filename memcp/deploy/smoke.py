"""The first memory, over MCP — what C2's number is measured to.

A deployment that answers `/health` is not the claim. The claim is that a client can
store a memory and find it again, so this speaks the same protocol an MCP client
speaks, against the published port, through the bearer gate, with the minted token.

It writes into a scope of its own and deletes that scope afterwards, so running it
against a deployment holding real memories leaves them alone.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

SMOKE_AGENT_ID = "memcp-smoke"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class SmokeError(RuntimeError):
    """The round trip did not complete. The message says which step failed."""


@dataclass
class SmokeResult:
    seconds: float
    memory_id: str
    matched: str


def _parse(response: httpx.Response) -> dict[str, Any]:
    """Read a JSON-RPC result from either a JSON body or an SSE frame."""
    body = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise SmokeError(f"no data frame in event stream:\n{body[:400]}")
    return json.loads(body)


def _rpc(
    client: httpx.Client, url: str, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    response = client.post(url, json=payload, headers=HEADERS)
    if response.status_code == 401:
        raise SmokeError(
            "the deployment rejected the token. If you rotated it, re-read it from "
            "the deployment's .env."
        )
    if response.status_code >= 400:
        raise SmokeError(f"{method} returned HTTP {response.status_code}:\n{response.text[:400]}")
    message = _parse(response)
    if "error" in message:
        raise SmokeError(f"{method} failed: {message['error']}")
    return message.get("result", {})


def _tool(client: httpx.Client, url: str, name: str, arguments: dict[str, Any]) -> Any:
    result = _rpc(client, url, "tools/call", {"name": name, "arguments": arguments})
    if result.get("isError"):
        raise SmokeError(f"{name} returned an error: {result.get('content')}")
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return content[0]["text"]
    return result


def first_memory(url: str, token: str, *, timeout: float = 60.0) -> SmokeResult:
    """add_memory then search_memory over MCP. Returns the wall-clock it took."""
    marker = uuid.uuid4().hex[:12]
    content = f"memcp provisioning smoke check {marker}"
    started = time.monotonic()

    with httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}"}) as client:
        _rpc(
            client,
            url,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "memcp-smoke", "version": "1"},
            },
        )
        added = _tool(
            client,
            url,
            "add_memory",
            {
                "content": content,
                "scope": {"agent_id": SMOKE_AGENT_ID},
                # Verbatim: extraction is an LLM behaviour and may legitimately store
                # nothing, which would make this check about the model rather than
                # about the deployment.
                "infer": False,
            },
        )
        found = _tool(
            client,
            url,
            "search_memory",
            {"query": marker, "scope": {"agent_id": SMOKE_AGENT_ID}},
        )
        seconds = time.monotonic() - started

        memories = found.get("results", []) if isinstance(found, dict) else []
        matched = next((m for m in memories if marker in m.get("content", "")), None)
        if matched is None:
            raise SmokeError(
                f"add_memory succeeded but search_memory did not return it back.\n"
                f"add: {json.dumps(added)[:300]}\nsearch: {json.dumps(found)[:300]}"
            )

        _tool(client, url, "delete_all_memories", {"scope": {"agent_id": SMOKE_AGENT_ID}})

    return SmokeResult(seconds=seconds, memory_id=str(matched.get("id", "")), matched=content)
