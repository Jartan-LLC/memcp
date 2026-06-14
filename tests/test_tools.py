"""Tool handler tests against the in-memory backend.

Validates the MCP tool layer independently of any real backend.
Tests cover: tool registration, canonical responses, scope security,
error handling, and capability-gated optional tools.
"""

from __future__ import annotations

from typing import Any

import pytest

from memcp.backend.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.tools import register_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeMCP:
    """Minimal stand-in for FastMCP that captures registered tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def tool(self, description: str = "", annotations: dict | None = None):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn

        return decorator

    async def call(self, name: str, **kwargs) -> Any:
        return await self._tools[name](**kwargs)

    @property
    def tool_names(self) -> set[str]:
        return set(self._tools.keys())


@pytest.fixture
def mcp_with_tools(config: Config, backend: InMemoryBackend) -> tuple[FakeMCP, InMemoryBackend]:
    mcp = FakeMCP()
    register_tools(mcp, backend, config)
    return mcp, backend


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


async def test_universal_tools_registered(mcp_with_tools):
    mcp, _ = mcp_with_tools
    universal = {
        "add_memory",
        "search_memory",
        "delete_memory",
        "delete_all_memories",
        "memory_status",
        "export_memories",
    }
    assert universal <= mcp.tool_names


async def test_optional_tools_registered(mcp_with_tools):
    mcp, _ = mcp_with_tools
    optional = {
        "get_memory",
        "update_memory",
        "list_memories",
        "memory_history",
        "memory_entities",
    }
    assert optional <= mcp.tool_names


# ---------------------------------------------------------------------------
# add_memory
# ---------------------------------------------------------------------------


async def test_add_memory_returns_result(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="I like Python")
    assert "results" in result
    assert len(result["results"]) == 1
    assert result["results"][0]["id"]
    assert result["results"][0]["status"] == "ready"


async def test_add_memory_with_scope(mcp_with_tools):
    mcp, backend = mcp_with_tools
    result = await mcp.call("add_memory", content="scoped fact", scope={"agent_id": "a1"})
    assert "results" in result
    listing = await backend.list_memories("test_user", scope={"agent_id": "a1"})
    assert len(listing.memories) == 1


async def test_add_memory_strips_user_id_from_scope(mcp_with_tools):
    mcp, backend = mcp_with_tools
    result = await mcp.call(
        "add_memory",
        content="scope attack",
        scope={"user_id": "attacker", "agent_id": "a1"},
    )
    assert "results" in result
    listing = await backend.list_memories("test_user")
    assert len(listing.memories) == 1
    attacker = await backend.list_memories("attacker")
    assert len(attacker.memories) == 0


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


async def test_search_memory_finds_content(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="My favorite editor is vim")
    result = await mcp.call("search_memory", query="editor")
    assert len(result["results"]) >= 1
    assert any("vim" in r["content"] for r in result["results"])


async def test_search_memory_returns_canonical_shape(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="test content")
    result = await mcp.call("search_memory", query="test")
    memory = result["results"][0]
    assert "id" in memory
    assert "content" in memory
    assert "score" in memory
    assert "scope" in memory
    assert "metadata" in memory
    assert "created_at" in memory
    assert "updated_at" in memory


async def test_search_memory_empty_results(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="nonexistent")
    assert result["results"] == []


async def test_search_memory_strips_user_id(mcp_with_tools):
    mcp, backend = mcp_with_tools
    # Add as test_user (via tool) and separately as attacker (directly)
    await mcp.call("add_memory", content="user secret data")
    await backend.add("attacker", "attacker secret data")
    # Search with user_id=attacker in scope — should be stripped,
    # so we get test_user's data, not attacker's
    result = await mcp.call("search_memory", query="secret", scope={"user_id": "attacker"})
    contents = [r["content"] for r in result["results"]]
    assert "user secret data" in contents
    assert "attacker secret data" not in contents


# ---------------------------------------------------------------------------
# delete_memory
# ---------------------------------------------------------------------------


async def test_delete_memory_success(mcp_with_tools):
    mcp, _ = mcp_with_tools
    added = await mcp.call("add_memory", content="to delete")
    memory_id = added["results"][0]["id"]
    result = await mcp.call("delete_memory", memory_id=memory_id)
    assert result["deleted"] is True


async def test_delete_memory_invalid_id(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("delete_memory", memory_id="../../bad")
    assert result["error"]["code"] == "validation_error"


async def test_delete_memory_not_found(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("delete_memory", memory_id="nonexistent-valid-id")
    assert result["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# delete_all_memories
# ---------------------------------------------------------------------------


async def test_delete_all_memories_with_scope(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="a1 memory", scope={"agent_id": "a1"})
    await mcp.call("add_memory", content="a2 memory", scope={"agent_id": "a2"})
    result = await mcp.call("delete_all_memories", scope={"agent_id": "a1"})
    assert "deleted_count" in result
    assert result["deleted_count"] == 1
    # Verify target gone, non-target survived
    remaining = await mcp.call("list_memories")
    contents = [m["content"] for m in remaining["memories"]]
    assert "a1 memory" not in contents
    assert "a2 memory" in contents


async def test_delete_all_memories_empty_scope_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("delete_all_memories", scope={})
    assert result["error"]["code"] == "scope_required"


async def test_delete_all_memories_user_id_stripped(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("delete_all_memories", scope={"user_id": "attacker"})
    assert result["error"]["code"] == "scope_required"


# ---------------------------------------------------------------------------
# memory_status
# ---------------------------------------------------------------------------


async def test_memory_status(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("memory_status")
    assert result["backend"] == "mem0"
    assert result["version"]
    assert isinstance(result["capabilities"], list)
    assert isinstance(result["scope_keys"], list)


# ---------------------------------------------------------------------------
# get_memory
# ---------------------------------------------------------------------------


async def test_get_memory_success(mcp_with_tools):
    mcp, _ = mcp_with_tools
    added = await mcp.call("add_memory", content="fetch me")
    memory_id = added["results"][0]["id"]
    result = await mcp.call("get_memory", memory_id=memory_id)
    assert result["content"] == "fetch me"
    assert result["id"] == memory_id


async def test_get_memory_not_found(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("get_memory", memory_id="nonexistent-valid-id")
    assert result["error"]["code"] == "not_found"


async def test_get_memory_invalid_id(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("get_memory", memory_id="bad/id")
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# update_memory
# ---------------------------------------------------------------------------


async def test_update_memory_success(mcp_with_tools):
    mcp, _ = mcp_with_tools
    added = await mcp.call("add_memory", content="original")
    memory_id = added["results"][0]["id"]
    result = await mcp.call("update_memory", memory_id=memory_id, content="updated")
    assert result["content"] == "updated"


async def test_update_memory_invalid_id(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("update_memory", memory_id="bad id!", content="x")
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# list_memories
# ---------------------------------------------------------------------------


async def test_list_memories_returns_all(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="one")
    await mcp.call("add_memory", content="two")
    result = await mcp.call("list_memories")
    assert len(result["memories"]) == 2


async def test_list_memories_invalid_cursor(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("list_memories", cursor="not-a-number")
    assert result["error"]["code"] == "validation_error"


async def test_list_memories_with_scope(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="scoped", scope={"agent_id": "x"})
    await mcp.call("add_memory", content="unscoped")
    result = await mcp.call("list_memories", scope={"agent_id": "x"})
    assert len(result["memories"]) == 1


# ---------------------------------------------------------------------------
# memory_history
# ---------------------------------------------------------------------------


async def test_memory_history(mcp_with_tools):
    mcp, _ = mcp_with_tools
    added = await mcp.call("add_memory", content="original")
    memory_id = added["results"][0]["id"]
    await mcp.call("update_memory", memory_id=memory_id, content="changed")
    result = await mcp.call("memory_history", memory_id=memory_id)
    assert len(result["history"]) == 2
    assert result["history"][0]["action"] == "created"
    assert result["history"][1]["action"] == "updated"


async def test_memory_history_invalid_id(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("memory_history", memory_id="bad/id")
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# memory_entities
# ---------------------------------------------------------------------------


async def test_memory_entities_returns_structure(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="entity test fact")
    result = await mcp.call("memory_entities")
    assert "entities" in result
    assert "relationships" in result
    assert len(result["entities"]) >= 1
    assert result["entities"][0]["id"] == "test_user"
    assert result["entities"][0]["total_memories"] >= 1


# ---------------------------------------------------------------------------
# Error path coverage
# ---------------------------------------------------------------------------


async def test_update_memory_not_found(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("update_memory", memory_id="nonexistent-valid-id", content="x")
    assert result["error"]["code"] == "not_found"


async def test_memory_history_not_found(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("memory_history", memory_id="nonexistent-valid-id")
    # in-memory backend returns empty history for unknown IDs
    assert result["history"] == []


async def test_add_memory_empty_extraction(mcp_with_tools):
    """When backend returns empty results, tool returns guidance string."""
    mcp, backend = mcp_with_tools

    # Patch add to return empty (simulating infer=true extracting nothing)
    original_add = backend.add

    async def empty_add(*args, **kwargs):
        return []

    backend.add = empty_add
    result = await mcp.call("add_memory", content="something")
    assert isinstance(result, str)
    assert "nothing was stored" in result.lower()
    backend.add = original_add


# ---------------------------------------------------------------------------
# Nested filter rejection
# ---------------------------------------------------------------------------


async def test_add_memory_rejects_nested_filters(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="test", scope={"AND": [{"a": 1}]})
    assert result["error"]["code"] == "nested_filter"


async def test_search_memory_rejects_nested_filters(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="test", scope={"OR": [{"a": 1}]})
    assert result["error"]["code"] == "nested_filter"


async def test_delete_all_rejects_nested_filters(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("delete_all_memories", scope={"NOT": {"a": 1}, "agent_id": "x"})
    assert result["error"]["code"] == "nested_filter"


# ---------------------------------------------------------------------------
# Invalid scope keys
# ---------------------------------------------------------------------------


async def test_add_memory_rejects_unknown_scope_keys(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="test", scope={"bogus_key": "val"})
    assert result["error"]["code"] == "invalid_scope"


async def test_search_rejects_unknown_scope_keys(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="test", scope={"bad": "val"})
    assert result["error"]["code"] == "invalid_scope"


async def test_valid_scope_keys_pass(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="scoped", scope={"agent_id": "a1"})
    result = await mcp.call("search_memory", query="scoped", scope={"agent_id": "a1"})
    assert len(result["results"]) >= 1


# ---------------------------------------------------------------------------
# Backend error forwarding
# ---------------------------------------------------------------------------


def _patch_raise(backend, method_name: str, status: int):
    """Patch a backend method to raise MemoryAPIError."""
    from memcp.types import MemoryAPIError

    async def raiser(*args, **kwargs):
        raise MemoryAPIError(status, f"simulated {status}")

    setattr(backend, method_name, raiser)


async def test_backend_error_503_is_retryable(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "search", 503)
    result = await mcp.call("search_memory", query="test")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_400_not_retryable(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "search", 400)
    result = await mcp.call("search_memory", query="test")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is False


async def test_backend_error_on_add(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "add", 503)
    result = await mcp.call("add_memory", content="test")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_on_delete(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "delete", 502)
    result = await mcp.call("delete_memory", memory_id="some-valid-id")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_on_delete_4xx(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "delete", 403)
    result = await mcp.call("delete_memory", memory_id="some-valid-id")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is False


async def test_backend_error_on_get(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "get", 503)
    result = await mcp.call("get_memory", memory_id="some-valid-id")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_on_delete_all(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "delete_all", 500)
    result = await mcp.call("delete_all_memories", scope={"agent_id": "x"})
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_timeout_returns_timeout_code(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "search", 408)
    result = await mcp.call("search_memory", query="test")
    assert result["error"]["code"] == "timeout"
    assert result["error"]["retry"] is True


async def test_backend_error_on_list_memories(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "list_memories", 503)
    result = await mcp.call("list_memories")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_on_update(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "update", 502)
    result = await mcp.call("update_memory", memory_id="some-valid-id", content="x")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_on_history(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "history", 500)
    result = await mcp.call("memory_history", memory_id="some-valid-id")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_backend_error_on_entities(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "entities", 503)
    result = await mcp.call("memory_entities")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


# ---------------------------------------------------------------------------
# Multi-tenant isolation (tool layer)
# ---------------------------------------------------------------------------


async def test_tenant_isolation_through_tools(mcp_with_tools):
    """Two different tenants see completely separate memory pools via tools."""
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    # Alice adds a memory
    tok = set_tenant("alice")
    await mcp.call("add_memory", content="alice secret fact")
    reset_tenant(tok)

    # Bob adds a memory
    tok = set_tenant("bob")
    await mcp.call("add_memory", content="bob secret fact")
    reset_tenant(tok)

    # Alice searches — should only see her own
    tok = set_tenant("alice")
    result = await mcp.call("search_memory", query="secret fact")
    contents = [r["content"] for r in result["results"]]
    assert "alice secret fact" in contents
    assert "bob secret fact" not in contents
    reset_tenant(tok)

    # Bob searches — should only see his own
    tok = set_tenant("bob")
    result = await mcp.call("search_memory", query="secret fact")
    contents = [r["content"] for r in result["results"]]
    assert "bob secret fact" in contents
    assert "alice secret fact" not in contents
    reset_tenant(tok)


async def test_tenant_isolation_list(mcp_with_tools):
    """list_memories respects tenant boundaries."""
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    tok = set_tenant("user_x")
    await mcp.call("add_memory", content="x data")
    reset_tenant(tok)

    tok = set_tenant("user_y")
    await mcp.call("add_memory", content="y data")
    listing = await mcp.call("list_memories")
    contents = [m["content"] for m in listing["memories"]]
    assert "y data" in contents
    assert "x data" not in contents
    reset_tenant(tok)


async def test_tenant_isolation_delete(mcp_with_tools):
    """One tenant cannot delete another's memories."""
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    tok = set_tenant("owner")
    added = await mcp.call("add_memory", content="owned data")
    memory_id = added["results"][0]["id"]
    reset_tenant(tok)

    tok = set_tenant("attacker")
    result = await mcp.call("delete_memory", memory_id=memory_id)
    assert result["error"]["code"] == "not_found"
    reset_tenant(tok)

    # Verify still exists for owner
    tok = set_tenant("owner")
    result = await mcp.call("get_memory", memory_id=memory_id)
    assert result["content"] == "owned data"
    reset_tenant(tok)


# ---------------------------------------------------------------------------
# Input validation bounds
# ---------------------------------------------------------------------------


async def test_empty_content_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="")
    assert result["error"]["code"] == "validation_error"


async def test_empty_query_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="")
    assert result["error"]["code"] == "validation_error"


async def test_zero_limit_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="test", limit=0)
    assert result["error"]["code"] == "validation_error"


async def test_negative_limit_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("list_memories", limit=-1)
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# export_memories
# ---------------------------------------------------------------------------


async def test_export_memories_returns_all(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="export one")
    await mcp.call("add_memory", content="export two")
    await mcp.call("add_memory", content="export three")
    result = await mcp.call("export_memories")
    assert result["count"] == 3
    assert len(result["memories"]) == 3
    contents = {m["content"] for m in result["memories"]}
    assert contents == {"export one", "export two", "export three"}


async def test_export_memories_empty(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("export_memories")
    assert result["count"] == 0
    assert result["memories"] == []


async def test_export_memories_tenant_isolation(mcp_with_tools):
    """Export only returns the current tenant's memories."""
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    tok = set_tenant("exporter")
    await mcp.call("add_memory", content="my data")
    reset_tenant(tok)

    tok = set_tenant("other_user")
    await mcp.call("add_memory", content="their data")
    result = await mcp.call("export_memories")
    contents = {m["content"] for m in result["memories"]}
    assert "their data" in contents
    assert "my data" not in contents
    reset_tenant(tok)
