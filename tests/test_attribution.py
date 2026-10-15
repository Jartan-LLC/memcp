"""Server-side author attribution: the seat stamped on a write comes from the resolved
token, never from the caller.

Before this landed, nothing in memcp distinguished which seat wrote a memory: two
principals sharing a tenant (the live shape of SEC-2026-0038 — one bearer token for
fifteen agents) were indistinguishable in every read, and a caller's `metadata` dict
reached storage and came back out completely unvalidated. That made the shared store
an anonymous instruction channel — a memory injected by any seat looked exactly like
one any other seat wrote, and a caller could claim any label it liked. These tests
drive the MCP tool surface (not backend internals).
"""

from __future__ import annotations

from typing import Any

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.identity import reset_tenant, set_tenant
from memcp.policy import Policy
from memcp.tools import register_tools
from memcp.types import SYSTEM_METADATA_KEY


class FakeMCP:
    """Minimal stand-in for MCPServer that captures registered tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def tool(self, **kwargs: Any):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn

        return decorator

    async def call(self, name: str, **kwargs) -> Any:
        return await self._tools[name](**kwargs)


@pytest.fixture
def mcp_with_tools(config: Config, backend: InMemoryBackend) -> tuple[FakeMCP, InMemoryBackend]:
    mcp = FakeMCP()
    register_tools(mcp, Policy(backend), config)
    return mcp, backend


async def test_add_memory_stamps_the_resolved_seat(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="a fact")
    result = await mcp.call("search_memory", query="fact")
    memory = result["results"][0]
    assert memory["author"] == "test_user"
    assert "attributed" not in memory


async def test_search_result_is_unattributed_when_backend_holds_no_stamp(mcp_with_tools):
    """A row written before this field existed (or by any caller that bypassed the
    auth layer) comes back null, not guessed."""
    mcp, backend = mcp_with_tools
    await backend.add("test_user", "a pre-existing row", infer=False)  # no system kwarg
    result = await mcp.call("search_memory", query="pre-existing")
    memory = result["results"][0]
    assert memory["author"] is None


async def test_add_memory_caller_cannot_forge_author_via_metadata(mcp_with_tools):
    """The reserved key is stripped on the way in and the resolved seat wins —
    a caller cannot claim to be a different, more-trusted seat."""
    mcp, _ = mcp_with_tools
    await mcp.call(
        "add_memory",
        content="spoof attempt",
        metadata={SYSTEM_METADATA_KEY: {"author": "root"}, "note": "kept"},
    )
    result = await mcp.call("search_memory", query="spoof")
    memory = result["results"][0]
    assert memory["author"] == "test_user"
    assert memory["metadata"] == {"note": "kept"}
    assert SYSTEM_METADATA_KEY not in memory["metadata"]


async def test_update_memory_relabels_to_the_updating_principal(mcp_with_tools):
    """Attribution-laundering repro: an update must never keep the original
    writer's label — that would let an attacker edit a trusted seat's memory and
    have it keep reading as trusted.

    Same tenant, different seat throughout: update_memory only reaches a memory
    within its own tenant (a cross-tenant update is a separate, already-tested
    isolation boundary, not what this test is about), and this is the actual
    shape one shared bearer token produces (SEC-2026-0038).
    """
    from memcp.identity import Principal, set_principal

    mcp, _ = mcp_with_tools
    tok = set_principal(Principal(tenant="shared", seat="owner"))
    added = await mcp.call("add_memory", content="original", infer=False)
    memory_id = added["results"][0]["id"]
    reset_tenant(tok)

    tok = set_principal(Principal(tenant="shared", seat="attacker"))
    result = await mcp.call("update_memory", memory_id=memory_id, content="tampered")
    reset_tenant(tok)

    assert result["author"] == "attacker"
    assert result["author"] != "owner"


async def test_update_memory_re_stamps_even_when_metadata_is_not_touched(mcp_with_tools):
    """update_memory's own contract is that omitted metadata is left alone — the
    re-stamp must not turn 'I'm not touching metadata' into 'wipe it'."""
    from memcp.identity import Principal, set_principal

    mcp, _ = mcp_with_tools
    tok = set_principal(Principal(tenant="shared", seat="owner"))
    added = await mcp.call(
        "add_memory", content="has metadata", metadata={"tag": "keep-me"}, infer=False
    )
    memory_id = added["results"][0]["id"]
    reset_tenant(tok)

    tok = set_principal(Principal(tenant="shared", seat="updater"))
    result = await mcp.call("update_memory", memory_id=memory_id, content="edited")
    reset_tenant(tok)

    assert result["metadata"] == {"tag": "keep-me"}
    assert result["author"] == "updater"


async def test_memory_history_entries_carry_the_acting_principal(mcp_with_tools):
    from memcp.identity import Principal, set_principal

    mcp, _ = mcp_with_tools
    tok = set_principal(Principal(tenant="shared", seat="creator"))
    added = await mcp.call("add_memory", content="v1", infer=False)
    memory_id = added["results"][0]["id"]
    reset_tenant(tok)

    tok = set_principal(Principal(tenant="shared", seat="editor"))
    await mcp.call("update_memory", memory_id=memory_id, content="v2")
    reset_tenant(tok)

    tok = set_principal(Principal(tenant="shared", seat="reader"))
    history = (await mcp.call("memory_history", memory_id=memory_id))["history"]
    reset_tenant(tok)

    assert [h["author"] for h in history] == ["creator", "editor"]


async def test_import_memories_stamps_the_importing_principal_not_an_incoming_author(
    mcp_with_tools,
):
    """export_memories now emits `author` on every entry, so a raw export payload
    is valid import_memories input carrying someone else's author label. Import
    must never honour it — the round trip does not preserve author, by design."""
    mcp, _ = mcp_with_tools
    tok = set_tenant("original_writer")
    await mcp.call("add_memory", content="carried over", infer=False)
    exported = await mcp.call("export_memories")
    reset_tenant(tok)
    assert exported["memories"][0]["author"] == "original_writer"

    tok = set_tenant("importer")
    result = await mcp.call(
        "import_memories", memories=exported["memories"], on_conflict="duplicate"
    )
    assert result["imported"] == 1
    listing = await mcp.call("list_memories")
    reset_tenant(tok)

    imported = next(m for m in listing["memories"] if m["content"] == "carried over")
    assert imported["author"] == "importer"
    assert imported["author"] != "original_writer"


async def test_import_memories_overwrite_re_stamps_author_too(mcp_with_tools):
    """on_conflict='overwrite' reaches backend.update — the same laundering repro
    as test_update_memory_relabels_to_the_updating_principal, through import.

    Dedup only matches within one tenant, so both calls share a tenant here and
    differ only in seat — the shape a shared bearer token actually produces
    (SEC-2026-0038: one tenant, many seats).
    """
    from memcp.identity import Principal, set_principal

    mcp, _ = mcp_with_tools
    tok = set_principal(Principal(tenant="shared", seat="owner"))
    await mcp.call("add_memory", content="dup content", infer=False)
    reset_tenant(tok)

    tok = set_principal(Principal(tenant="shared", seat="importer"))
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "dup content"}],
        on_conflict="overwrite",
    )
    assert result["imported"] == 1
    listing = await mcp.call("list_memories")
    reset_tenant(tok)

    memory = next(m for m in listing["memories"] if m["content"] == "dup content")
    assert memory["author"] == "importer"


async def test_no_auth_dev_mode_write_is_not_attributed(mcp_with_tools):
    """With no resolver, nothing authenticated the caller, so a write made
    under the dev-mode fallback principal must record no author — one would
    tell a reader "the server resolved this seat" when it resolved nothing at
    all.

    No `tenant_context` fixture override here: the default `DEFAULT_PRINCIPAL`
    context (unset contextvar) is exactly the no-`MEMCP_AUTH_TOKENS` shape.
    """
    from memcp.identity import DEFAULT_PRINCIPAL, reset_tenant, set_principal

    mcp, _ = mcp_with_tools
    tok = set_principal(DEFAULT_PRINCIPAL)
    try:
        await mcp.call("add_memory", content="no auth configured")
        result = await mcp.call("search_memory", query="no auth")
    finally:
        reset_tenant(tok)

    memory = result["results"][0]
    assert memory["author"] is None


async def test_real_token_onto_the_default_tenant_name_is_still_attributed(mcp_with_tools):
    """A real, resolved token that happens to map onto memcp's own dev-mode
    tenant name ('default_user') is not the dev-mode fallback — something did
    authenticate it — so it must not be misread as unattributed. The natural
    way this mapping arises: migrating a no-auth deployment onto tokens while
    keeping existing data's tenant name."""
    from memcp.http.auth import StaticResolver
    from memcp.identity import reset_tenant, set_principal

    mcp, _ = mcp_with_tools
    resolved = await StaticResolver.from_env("realtoken:default_user").resolve("realtoken")
    assert resolved is not None
    tok = set_principal(resolved)
    try:
        await mcp.call("add_memory", content="authenticated as default_user")
        result = await mcp.call("search_memory", query="authenticated as default")
    finally:
        reset_tenant(tok)

    memory = result["results"][0]
    assert memory["author"] == "default_user"
