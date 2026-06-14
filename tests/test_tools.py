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
from memcp.types import MemoryAPIError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeMCP:
    """Minimal stand-in for FastMCP that captures registered tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def tool(self, **kwargs: Any):
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
        "export_memories",
        "import_memories",
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
    assert result["backend"] == "in_memory"
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
    assert result["error"]["code"] == "not_found"


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


async def test_scope_value_invalid_type_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="test", scope={"agent_id": ["a", "b"]})
    assert result["error"]["code"] == "validation_error"
    assert "string or number" in result["error"]["message"]


async def test_scope_too_many_keys_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    scope = {f"key_{i}": "val" for i in range(20)}
    result = await mcp.call("add_memory", content="test", scope=scope)
    assert result["error"]["code"] == "validation_error"
    assert "too many" in result["error"]["message"].lower()


async def test_scope_key_too_long_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="test", scope={"a" * 100: "val"})
    assert result["error"]["code"] == "validation_error"
    assert "key too long" in result["error"]["message"].lower()


async def test_scope_value_too_long_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="test", scope={"agent_id": "x" * 300})
    assert result["error"]["code"] == "validation_error"
    assert "value too long" in result["error"]["message"].lower()


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


async def test_tenant_isolation_update(mcp_with_tools):
    """One tenant cannot update another's memories."""
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    tok = set_tenant("owner")
    added = await mcp.call("add_memory", content="original")
    memory_id = added["results"][0]["id"]
    reset_tenant(tok)

    tok = set_tenant("attacker")
    result = await mcp.call("update_memory", memory_id=memory_id, content="hijacked")
    assert result["error"]["code"] == "not_found"
    reset_tenant(tok)

    # Verify unchanged for owner
    tok = set_tenant("owner")
    result = await mcp.call("get_memory", memory_id=memory_id)
    assert result["content"] == "original"
    reset_tenant(tok)


async def test_tenant_isolation_history(mcp_with_tools):
    """One tenant cannot read another's memory history."""
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    tok = set_tenant("owner")
    added = await mcp.call("add_memory", content="private data")
    memory_id = added["results"][0]["id"]
    reset_tenant(tok)

    tok = set_tenant("attacker")
    result = await mcp.call("memory_history", memory_id=memory_id)
    assert result["error"]["code"] == "not_found"
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


async def test_limit_upper_bound_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="test", limit=1001)
    assert result["error"]["code"] == "validation_error"
    assert "maximum" in result["error"]["message"]


async def test_whitespace_content_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="   ")
    assert result["error"]["code"] == "validation_error"


async def test_whitespace_query_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="   ")
    assert result["error"]["code"] == "validation_error"


async def test_threshold_out_of_range(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="test", threshold=1.5)
    assert result["error"]["code"] == "validation_error"
    assert "threshold" in result["error"]["message"]


async def test_threshold_negative_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="test", threshold=-0.1)
    assert result["error"]["code"] == "validation_error"


async def test_memory_id_too_long_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("get_memory", memory_id="a" * 129)
    assert result["error"]["code"] == "validation_error"


async def test_content_too_long_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("add_memory", content="x" * 100_001)
    assert result["error"]["code"] == "validation_error"
    assert "maximum" in result["error"]["message"]


async def test_query_too_long_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("search_memory", query="x" * 10_001)
    assert result["error"]["code"] == "validation_error"
    assert "maximum" in result["error"]["message"]


# ---------------------------------------------------------------------------
# import_memories
# ---------------------------------------------------------------------------


async def test_import_memories_basic(mcp_with_tools):
    mcp, _ = mcp_with_tools
    memories = [
        {"content": "fact one"},
        {"content": "fact two", "scope": {"agent_id": "a1"}},
        {"content": "fact three", "metadata": {"source": "test"}},
    ]
    result = await mcp.call("import_memories", memories=memories)
    assert result["imported"] == 3
    assert len(result["results"]) == 3
    assert result["errors"] == []
    assert result["skipped"] == 0


async def test_import_memories_over_limit(mcp_with_tools):
    from memcp.types import MAX_IMPORT

    mcp, _ = mcp_with_tools
    memories = [{"content": f"mem {i}"} for i in range(MAX_IMPORT + 1)]
    result = await mcp.call("import_memories", memories=memories)
    assert result["error"]["code"] == "validation_error"


async def test_import_memories_empty_rejected(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("import_memories", memories=[])
    assert result["error"]["code"] == "validation_error"


async def test_import_memories_invalid_on_conflict(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("import_memories", memories=[{"content": "x"}], on_conflict="bad")
    assert result["error"]["code"] == "validation_error"
    assert "on_conflict" in result["error"]["message"]


async def test_import_memories_skips_bad_entries(mcp_with_tools):
    mcp, _ = mcp_with_tools
    memories = [
        {"content": "good"},
        {"content": ""},
        {"content": "   "},
        {"no_content": True},
        {"content": "also good"},
    ]
    result = await mcp.call("import_memories", memories=memories)
    assert result["imported"] == 2
    assert len(result["errors"]) == 3


async def test_import_memories_stores_verbatim(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call("import_memories", memories=[{"content": "exact text"}])
    assert result["imported"] == 1
    search = await mcp.call("search_memory", query="exact text")
    assert any(r["content"] == "exact text" for r in search["results"])


async def test_import_memories_dedup_skip(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="existing fact", infer=False)
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "existing fact"}, {"content": "new fact"}],
        on_conflict="skip",
    )
    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert result["skipped_details"][0]["existing_id"]


async def test_import_memories_dedup_overwrite(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="existing fact", infer=False)
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "existing fact", "metadata": {"updated": True}}],
        on_conflict="overwrite",
    )
    assert result["imported"] == 1
    assert result["results"][0]["action"] == "updated"


async def test_import_memories_dedup_duplicate(mcp_with_tools):
    mcp, _ = mcp_with_tools
    await mcp.call("add_memory", content="existing fact", infer=False)
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "existing fact"}],
        on_conflict="duplicate",
    )
    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["results"][0]["action"] == "created"


async def test_import_memories_within_batch_dedup(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "same"}, {"content": "same"}, {"content": "unique"}],
        on_conflict="skip",
    )
    assert result["imported"] == 2
    assert result["skipped"] == 1


async def test_import_memories_unknown_scope_key_per_entry(mcp_with_tools):
    mcp, _ = mcp_with_tools
    result = await mcp.call(
        "import_memories",
        memories=[
            {"content": "good", "scope": {"agent_id": "a1"}},
            {"content": "bad scope", "scope": {"bogus": "val"}},
        ],
        on_conflict="duplicate",
    )
    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1


async def test_import_memories_dedup_index_build_error(mcp_with_tools):
    mcp, backend = mcp_with_tools
    original = backend.list_memories

    async def fail_list(*args, **kwargs):
        raise MemoryAPIError(503, "backend down")

    backend.list_memories = fail_list
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "test"}],
        on_conflict="skip",
    )
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True
    backend.list_memories = original


async def test_import_memories_backend_error_per_entry(mcp_with_tools):
    mcp, backend = mcp_with_tools
    original_add = backend.add

    call_count = 0

    async def failing_add(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise MemoryAPIError(503, "backend down")
        return await original_add(*args, **kwargs)

    backend.add = failing_add
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "one"}, {"content": "two"}, {"content": "three"}],
        on_conflict="duplicate",
    )
    assert result["imported"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1
    backend.add = original_add


async def test_import_memories_overwrite_update_error(mcp_with_tools):
    mcp, backend = mcp_with_tools
    await mcp.call("add_memory", content="existing", infer=False)
    original = backend.update

    async def fail_update(*args, **kwargs):
        raise MemoryAPIError(503, "update failed")

    backend.update = fail_update
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "existing"}],
        on_conflict="overwrite",
    )
    assert result["imported"] == 0
    assert len(result["errors"]) == 1
    backend.update = original


async def test_import_memories_overwrite_requires_capability(config):
    """overwrite rejected when backend lacks update_memory capability."""
    from memcp.backend.in_memory import InMemoryBackend

    class NoUpdateBackend(InMemoryBackend):
        def capabilities(self):
            return super().capabilities() - {"update_memory"}

    backend = NoUpdateBackend()
    mcp = FakeMCP()
    register_tools(mcp, backend, config)
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "test"}],
        on_conflict="overwrite",
    )
    assert result["error"]["code"] == "not_supported"


async def test_import_memories_scope_injection_stripped(mcp_with_tools):
    """user_id in per-entry scope is stripped, memory stored for real tenant."""
    mcp, backend = mcp_with_tools
    result = await mcp.call(
        "import_memories",
        memories=[{"content": "attack data", "scope": {"user_id": "victim"}}],
        on_conflict="duplicate",
    )
    assert result["imported"] == 1
    # Verify stored for test_user, not victim
    listing = await backend.list_memories("test_user")
    assert any(m.content == "attack data" for m in listing.memories)
    victim = await backend.list_memories("victim")
    assert len(victim.memories) == 0


async def test_import_memories_tenant_isolation(mcp_with_tools):
    from memcp.auth import reset_tenant, set_tenant

    mcp, _ = mcp_with_tools

    tok = set_tenant("importer")
    await mcp.call("import_memories", memories=[{"content": "imported data"}])
    reset_tenant(tok)

    tok = set_tenant("other_user")
    search = await mcp.call("search_memory", query="imported data")
    assert len(search["results"]) == 0
    reset_tenant(tok)


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
    assert result["truncated"] is False
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


async def test_export_memories_backend_error(mcp_with_tools):
    mcp, backend = mcp_with_tools
    _patch_raise(backend, "list_memories", 503)
    result = await mcp.call("export_memories")
    assert result["error"]["code"] == "backend_error"
    assert result["error"]["retry"] is True


async def test_export_memories_over_limit(mcp_with_tools):
    mcp, backend = mcp_with_tools
    from memcp.types import MAX_EXPORT, ListResult, Memory

    async def big_list(*args, **kwargs):
        memories = [Memory(id=f"m-{i}", content=f"mem {i}") for i in range(MAX_EXPORT + 1)]
        return ListResult(memories=memories)

    backend.list_memories = big_list
    result = await mcp.call("export_memories")
    assert result["truncated"] is True
    assert result["count"] == MAX_EXPORT
    assert len(result["memories"]) == MAX_EXPORT
