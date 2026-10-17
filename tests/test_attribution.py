"""Server-side author attribution: the name stamped on a write is the acting key's,
never the caller's.

Two keys granted one space must be told apart in every read, and no caller may claim
another key's name: otherwise a shared space is an anonymous instruction channel. These
tests drive the MCP tool surface (not backend internals).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.http.auth import LOCAL_KEY, load_keys
from memcp.identity import reset_key, set_key
from memcp.policy import Policy
from memcp.tools import register_tools
from memcp.types import SYSTEM_METADATA_KEY
from tests.keys import make_key, write_keys_file


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


async def test_add_memory_stamps_the_acting_key(mcp_with_tools):
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
    """The reserved key is refused on the way in, naming it, and nothing is stored —
    a caller cannot claim to be a different, more-trusted key."""
    mcp, _ = mcp_with_tools
    refused = await mcp.call(
        "add_memory",
        content="spoof attempt",
        metadata={SYSTEM_METADATA_KEY: {"author": "root"}, "note": "kept"},
    )
    assert refused["error"]["code"] == "validation_error"
    assert SYSTEM_METADATA_KEY in refused["error"]["message"]
    result = await mcp.call("search_memory", query="spoof")
    assert result["results"] == []


async def test_update_memory_relabels_to_the_updating_key(mcp_with_tools):
    """Attribution-laundering repro: an update must never keep the original
    writer's label — that would let an attacker edit a trusted key's memory and
    have it keep reading as trusted.

    Two writer keys on one space: update_memory only reaches a memory in the key's
    own space (a cross-space update is a separate, already-tested isolation
    boundary, not what this test is about).
    """

    mcp, _ = mcp_with_tools
    tok = set_key(make_key("shared", name="owner"))
    added = await mcp.call("add_memory", content="original", infer=False)
    memory_id = added["results"][0]["id"]
    reset_key(tok)

    tok = set_key(make_key("shared", name="attacker"))
    result = await mcp.call("update_memory", memory_id=memory_id, content="tampered")
    reset_key(tok)

    assert result["author"] == "attacker"
    assert result["author"] != "owner"


async def test_update_memory_re_stamps_even_when_metadata_is_not_touched(mcp_with_tools):
    """update_memory's own contract is that omitted metadata is left alone — the
    re-stamp must not turn 'I'm not touching metadata' into 'wipe it'."""

    mcp, _ = mcp_with_tools
    tok = set_key(make_key("shared", name="owner"))
    added = await mcp.call(
        "add_memory", content="has metadata", metadata={"tag": "keep-me"}, infer=False
    )
    memory_id = added["results"][0]["id"]
    reset_key(tok)

    tok = set_key(make_key("shared", name="updater"))
    result = await mcp.call("update_memory", memory_id=memory_id, content="edited")
    reset_key(tok)

    assert result["metadata"] == {"tag": "keep-me"}
    assert result["author"] == "updater"


async def test_memory_history_entries_carry_the_acting_key(mcp_with_tools):

    mcp, _ = mcp_with_tools
    tok = set_key(make_key("shared", name="creator"))
    added = await mcp.call("add_memory", content="v1", infer=False)
    memory_id = added["results"][0]["id"]
    reset_key(tok)

    tok = set_key(make_key("shared", name="editor"))
    await mcp.call("update_memory", memory_id=memory_id, content="v2")
    reset_key(tok)

    tok = set_key(make_key("shared", "reader", name="reader"))
    history = (await mcp.call("memory_history", memory_id=memory_id))["history"]
    reset_key(tok)

    assert [h["author"] for h in history] == ["creator", "editor"]


async def test_import_memories_stamps_the_importing_key_not_an_incoming_author(
    mcp_with_tools,
):
    """export_memories now emits `author` on every entry, so a raw export payload
    is valid import_memories input carrying someone else's author label. Import
    must never honour it — the round trip does not preserve author, by design."""
    mcp, _ = mcp_with_tools
    tok = set_key(make_key("original_writer"))
    await mcp.call("add_memory", content="carried over", infer=False)
    exported = await mcp.call("export_memories")
    reset_key(tok)
    assert exported["memories"][0]["author"] == "original_writer"

    tok = set_key(make_key("importer"))
    result = await mcp.call(
        "import_memories", memories=exported["memories"], on_conflict="duplicate"
    )
    assert result["imported"] == 1
    listing = await mcp.call("list_memories")
    reset_key(tok)

    imported = next(m for m in listing["memories"] if m["content"] == "carried over")
    assert imported["author"] == "importer"
    assert imported["author"] != "original_writer"


async def test_import_memories_overwrite_re_stamps_author_too(mcp_with_tools):
    """on_conflict='overwrite' reaches backend.update — the same laundering repro
    as test_update_memory_relabels_to_the_updating_key, through import.

    Dedup only matches within one space, so both keys are granted the same one.
    """

    mcp, _ = mcp_with_tools
    tok = set_key(make_key("shared", name="owner"))
    await mcp.call("add_memory", content="dup content", infer=False)
    reset_key(tok)

    tok = set_key(make_key("shared", name="importer"))
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "dup content"}],
        on_conflict="overwrite",
    )
    assert result["imported"] == 1
    listing = await mcp.call("list_memories")
    reset_key(tok)

    memory = next(m for m in listing["memories"] if m["content"] == "dup content")
    assert memory["author"] == "importer"


async def test_a_dev_mode_write_is_attributed_to_the_local_key(mcp_with_tools):
    """Without a keys file every request is the key `local`, and its writes say so."""
    mcp, _ = mcp_with_tools
    tok = set_key(LOCAL_KEY)
    try:
        await mcp.call("add_memory", content="no keys file configured")
        result = await mcp.call("search_memory", query="no keys file")
    finally:
        reset_key(tok)

    memory = result["results"][0]
    assert memory["author"] == "local"


async def test_a_key_granted_the_dev_mode_space_is_attributed_by_its_own_name(
    mcp_with_tools, tmp_path: Path
):
    """A keys-file key granted `default_user`, the dev-mode space, writes as itself,
    not as `local`. The natural way this arises: moving a dev deployment onto keys
    while keeping its existing memories."""
    mcp, _ = mcp_with_tools
    path = write_keys_file(
        tmp_path / "keys.toml", ("migrated", "realtoken", "default_user", "writer")
    )
    resolved = load_keys(str(path)).resolve("realtoken")
    assert resolved is not None
    tok = set_key(resolved)
    try:
        await mcp.call("add_memory", content="authenticated as default_user")
        result = await mcp.call("search_memory", query="authenticated as default")
    finally:
        reset_key(tok)

    memory = result["results"][0]
    assert memory["author"] == "migrated"
