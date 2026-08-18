"""Server-side author attribution — SEC-2026-0094 conjunct 2, route 2 (JAR-723).

Before this landed, nothing in memcp distinguished which seat wrote a memory: two
principals sharing a tenant (the live shape of SEC-2026-0038 — one bearer token for
fifteen agents) were indistinguishable in every read, and a caller's `metadata` dict
reached storage and came back out completely unvalidated. That made the shared store
an anonymous instruction channel — a memory injected by any seat looked exactly like
one any other seat wrote, and a caller could claim any label it liked. These tests
drive the MCP tool surface (not backend internals).

Most fail against the pre-patch tree on a real assertion: `author`/`attributed` were
not keys `serialize_memory` produced at all, so the key-presence and equality
assertions raise `KeyError` there, not just return a different value. The four tests
that need `Principal`/`set_principal` import them locally rather than at module
scope — `seat` as an axis distinct from `tenant` is itself new in this fix, so
against the pre-patch tree those four fail on the import rather than on an
assertion; there is no pre-patch behaviour for "same tenant, different seat" to
assert against, because the tenant was the only identity axis that existed.
"""

from __future__ import annotations

from typing import Any

import pytest

from memcp.auth import reset_tenant, set_tenant
from memcp.backend.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.tools import register_tools

# Mirrors memcp.types._AUTHOR_METADATA_KEY, deliberately not imported: importing a
# reserved-metadata concept that does not exist pre-patch would turn the forgery
# test below into a collection error rather than a real assertion failure — the
# forged key must simply survive untouched in metadata on the unpatched tree.
_AUTHOR_METADATA_KEY = "_memcp_author"


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
    register_tools(mcp, backend, config)
    return mcp, backend


async def test_add_memory_stamps_the_resolved_seat(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="a fact")
    result = await mcp.call("search_memory", query="fact")
    memory = result["results"][0]
    assert memory["author"] == "test_user"
    assert memory["attributed"] is True


async def test_search_result_is_unattributed_when_backend_holds_no_stamp(mcp_with_tools):
    """A row written before this field existed (or by any caller that bypassed the
    auth layer) comes back null, not guessed."""
    mcp, backend = mcp_with_tools
    await backend.add("test_user", "a pre-existing row", infer=False)  # no author kwarg
    result = await mcp.call("search_memory", query="pre-existing")
    memory = result["results"][0]
    assert memory["author"] is None
    assert memory["attributed"] is False


async def test_add_memory_caller_cannot_forge_author_via_metadata(mcp_with_tools):
    """The reserved key is stripped on the way in and the resolved seat wins —
    a caller cannot claim to be a different, more-trusted seat."""
    mcp, _ = mcp_with_tools
    await mcp.call(
        "add_memory",
        content="spoof attempt",
        metadata={_AUTHOR_METADATA_KEY: "root", "note": "kept"},
    )
    result = await mcp.call("search_memory", query="spoof")
    memory = result["results"][0]
    assert memory["author"] == "test_user"
    assert memory["metadata"] == {"note": "kept"}
    assert _AUTHOR_METADATA_KEY not in memory["metadata"]


async def test_update_memory_relabels_to_the_updating_principal(mcp_with_tools):
    """Attribution-laundering repro: an update must never keep the original
    writer's label — that would let an attacker edit a trusted seat's memory and
    have it keep reading as trusted.

    Same tenant, different seat throughout: update_memory only reaches a memory
    within its own tenant (a cross-tenant update is a separate, already-tested
    isolation boundary, not what this test is about), and this is the actual
    shape one shared bearer token produces (SEC-2026-0038). `seat` as distinct
    from `tenant` is new in this fix, so this import is local — against the
    pre-patch tree this test errors on the import rather than on an assertion.
    """
    from memcp.auth import Principal, set_principal

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
    from memcp.auth import Principal, set_principal

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
    from memcp.auth import Principal, set_principal

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
    from memcp.auth import Principal, set_principal

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
    """Corin, JAR-723 finding 2: with no resolver, nothing authenticated the
    caller, so a write made under the dev-mode fallback principal must not
    claim `attributed: true` — that would tell a reader "the server resolved
    this seat" when it resolved nothing at all.

    No `tenant_context` fixture override here: the default `_DEFAULT_PRINCIPAL`
    context (unset contextvar) is exactly the no-`MEMCP_AUTH_TOKENS` shape.
    """
    from memcp.auth import _DEFAULT_PRINCIPAL, reset_tenant, set_principal

    mcp, _ = mcp_with_tools
    tok = set_principal(_DEFAULT_PRINCIPAL)
    try:
        await mcp.call("add_memory", content="no auth configured")
        result = await mcp.call("search_memory", query="no auth")
    finally:
        reset_tenant(tok)

    memory = result["results"][0]
    assert memory["author"] is None
    assert memory["attributed"] is False
