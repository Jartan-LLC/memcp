"""Required-method conformance — the six abstract methods plus the two declarations.

Every backend runs every test here. None of it is capability-gated, so nothing in
this module may skip.
"""

from __future__ import annotations

import uuid

import pytest

from memcp.backend.base import MemoryBackend
from memcp.conformance.capabilities import (
    ALL_CAPABILITIES,
    OPTIONAL_CAPABILITIES,
    REQUIRED_METHODS,
)
from memcp.types import AddResult, MemoryAPIError

MISSING_ID = "00000000-0000-4000-8000-000000000000"

# pytest-asyncio decides how to run a test at collection time, so the marker has
# to be here rather than added by a hook. See suite/conftest.py.
pytestmark = pytest.mark.asyncio


def _ids(result: AddResult | list[AddResult]) -> list[str]:
    items = result if isinstance(result, list) else [result]
    return [r.id for r in items]


async def test_implements_required_methods(backend: MemoryBackend):
    for name in REQUIRED_METHODS:
        assert callable(getattr(backend, name, None)), f"missing required method {name}()"


async def test_capabilities_are_known_names(backend: MemoryBackend):
    caps = backend.capabilities()
    assert isinstance(caps, set)
    unknown = caps - set(ALL_CAPABILITIES)
    assert not unknown, (
        f"capabilities() returned names the capability model does not know: {sorted(unknown)}. "
        f"Known: {list(ALL_CAPABILITIES)}"
    )


async def test_scope_keys_are_strings(backend: MemoryBackend):
    keys = backend.scope_keys()
    assert isinstance(keys, list)
    assert keys, "a backend must declare at least one scope key"
    assert all(isinstance(k, str) and k for k in keys)
    assert "user_id" not in keys, "user_id is the tenant, never a scope key"


async def test_health_reports_a_status(backend: MemoryBackend):
    status = await backend.health()
    assert status.status in ("healthy", "unhealthy"), status.status
    assert status.backend, "health() must name the backend"


async def test_add_returns_ids(backend: MemoryBackend, tenant: str):
    result = await backend.add(tenant, "conformance add returns an id", infer=False)
    ids = _ids(result)
    assert ids, "add() with infer=False must store something and return its id"
    assert all(isinstance(i, str) and i for i in ids)


async def test_add_then_search_finds_it(backend: MemoryBackend, tenant: str):
    content = "the conformance kingfisher nests behind the boiler room"
    await backend.add(tenant, content, infer=False)
    results = await backend.search(tenant, "conformance kingfisher boiler room", limit=10)
    assert any(m.content == content for m in results), (
        f"search did not return the memory just added; got {[m.content for m in results]}"
    )


async def test_add_stores_scope(backend: MemoryBackend, tenant: str):
    scope_key = backend.scope_keys()[0]
    scope = {scope_key: "conformance_scope_one"}
    content = "scoped conformance fact about the harbour crane"
    await backend.add(tenant, content, scope=scope, infer=False)

    hits = await backend.search(tenant, "scoped harbour crane", scope=scope, limit=10)
    assert any(m.content == content for m in hits), "scope-filtered search missed the memory"

    other = await backend.search(
        tenant, "scoped harbour crane", scope={scope_key: "conformance_scope_two"}, limit=10
    )
    assert not any(m.content == content for m in other), (
        "a memory in one scope was returned for a different scope"
    )


async def test_add_with_infer_returns_a_valid_shape(backend: MemoryBackend, tenant: str):
    """infer=True may extract nothing. Whatever comes back has to be well-formed."""
    result = await backend.add(
        tenant, "My name is Conformance Tester and I prefer UTC timestamps", infer=True
    )
    items = result if isinstance(result, list) else [result]
    assert all(isinstance(r, AddResult) and isinstance(r.id, str) for r in items)


async def test_search_rejects_nested_filters(backend: MemoryBackend, tenant: str):
    with pytest.raises(ValueError):
        await backend.search(tenant, "anything", scope={"AND": [{"agent_id": "a"}]})


async def test_search_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    content = "tenant isolation conformance marker phrase alpaca"
    await backend.add(tenant, content, infer=False)
    leaked = await backend.search(other_tenant, "isolation marker alpaca", limit=10)
    assert not any(m.content == content for m in leaked), (
        "another tenant's memory was returned by search"
    )


async def test_delete_removes_the_memory(backend: MemoryBackend, tenant: str):
    result = await backend.add(tenant, "conformance delete target", infer=False)
    memory_id = _ids(result)[0]
    assert await backend.delete(tenant, memory_id) is True
    remaining = await backend.search(tenant, "conformance delete target", limit=10)
    assert not any(m.id == memory_id for m in remaining)


async def test_delete_missing_raises_404(backend: MemoryBackend, tenant: str):
    with pytest.raises(MemoryAPIError) as exc:
        await backend.delete(tenant, MISSING_ID)
    assert exc.value.status == 404


async def test_delete_across_tenants_raises_404(
    backend: MemoryBackend, tenant: str, other_tenant: str
):
    result = await backend.add(tenant, "conformance cross tenant delete guard", infer=False)
    memory_id = _ids(result)[0]
    with pytest.raises(MemoryAPIError) as exc:
        await backend.delete(other_tenant, memory_id)
    assert exc.value.status == 404


async def test_delete_all_by_scope(backend: MemoryBackend, tenant: str):
    scope_key = backend.scope_keys()[0]
    keep_scope = {scope_key: f"keep_{uuid.uuid4().hex[:6]}"}
    drop_scope = {scope_key: f"drop_{uuid.uuid4().hex[:6]}"}
    await backend.add(tenant, "conformance memory that survives", scope=keep_scope, infer=False)
    await backend.add(tenant, "conformance memory that is purged", scope=drop_scope, infer=False)

    count = await backend.delete_all(tenant, drop_scope)
    assert count is None or isinstance(count, int), "delete_all returns a count or None"

    purged = await backend.search(tenant, "conformance memory purged", scope=drop_scope, limit=10)
    assert not purged, "delete_all left memories in the deleted scope"
    kept = await backend.search(tenant, "conformance memory survives", scope=keep_scope, limit=10)
    assert kept, "delete_all removed memories outside the requested scope"


async def test_delete_all_isolates_tenants(backend: MemoryBackend, tenant: str, other_tenant: str):
    scope_key = backend.scope_keys()[0]
    scope = {scope_key: f"shared_{uuid.uuid4().hex[:6]}"}
    content = "conformance other tenant memory must survive delete_all"
    await backend.add(other_tenant, content, scope=scope, infer=False)
    await backend.add(tenant, "conformance own tenant memory", scope=scope, infer=False)

    await backend.delete_all(tenant, scope)

    survivors = await backend.search(other_tenant, "other tenant survive delete", scope=scope)
    assert any(m.content == content for m in survivors), (
        "delete_all deleted another tenant's memories in the same scope"
    )


async def _call_optional(backend: MemoryBackend, method_name: str, tenant: str) -> object:
    method = getattr(backend, method_name)
    match method_name:
        case "get" | "history":
            return await method(tenant, MISSING_ID)
        case "update":
            return await method(tenant, MISSING_ID, "replacement")
        case _:
            return await method(tenant)


async def test_undeclared_capabilities_raise_not_implemented(backend: MemoryBackend, tenant: str):
    """A2's other half: what a backend does not claim, it must refuse outright.

    A backend that silently half-answers a method it did not declare is worse than
    one that raises. The tool layer never registers that tool, so the behaviour is
    unreachable in production and unverified by this suite — and callers inside memcp
    do reach backend methods directly, `memcp.migrate` among them.
    """
    declared = backend.capabilities()
    for capability, method_name in OPTIONAL_CAPABILITIES.items():
        if capability in declared:
            continue
        try:
            await _call_optional(backend, method_name, tenant)
        except NotImplementedError:
            continue
        except Exception as e:  # any other error is still not a refusal
            pytest.fail(
                f"capabilities() omits {capability!r} but {method_name}() raised "
                f"{type(e).__name__} instead of NotImplementedError: {e}. An undeclared "
                "method must refuse, not fail some other way."
            )
        pytest.fail(
            f"capabilities() omits {capability!r} but {method_name}() answered instead "
            "of raising NotImplementedError. If the backend genuinely supports it, "
            f"declare {capability!r}. If it inherits the implementation from another "
            f"backend and means not to expose it, override {method_name}() to raise "
            "NotImplementedError — narrowing by omission alone leaves working code no "
            "caller can reach and this suite cannot check."
        )
