"""Live cognee integration tests — the claims only a running cognee can settle.

Skipped unless COGNEE_API_BASE and COGNEE_TENANT_SECRET are set; `ci/cognee/up.sh`
stands up a server that satisfies both. The conformance suite covers the Protocol.
What is here is cognee-specific and would otherwise go unasserted:

- that a memory is findable the instant `add()` returns, with no second call;
- that `memory_entities` answers from a graph with edges in it, rather than from a
  node invented to fill the shape;
- that cognee's content-hash deduplication does not swallow a second memory.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import AsyncGenerator

import pytest

from memcp.backend.cognee import CogneeBackend

COGNEE_API_BASE = os.environ.get("COGNEE_API_BASE")
COGNEE_TENANT_SECRET = os.environ.get("COGNEE_TENANT_SECRET")

pytestmark = pytest.mark.skipif(
    not COGNEE_API_BASE or not COGNEE_TENANT_SECRET,
    reason="COGNEE_API_BASE and COGNEE_TENANT_SECRET not set",
)

GRAPH_TEXT = "Ada Lovelace worked with Charles Babbage on the Analytical Engine in London"


def _tenant() -> str:
    return f"live_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def cognee() -> AsyncGenerator[CogneeBackend]:
    assert COGNEE_API_BASE and COGNEE_TENANT_SECRET
    backend = CogneeBackend(COGNEE_API_BASE, COGNEE_TENANT_SECRET)
    yield backend
    await backend.close()


async def _wipe(backend: CogneeBackend, *tenants: str) -> None:
    for tenant in tenants:
        with contextlib.suppress(Exception):
            await backend.delete_all(tenant, {})


async def test_health_requires_the_isolation_posture(cognee: CogneeBackend):
    """Healthy means both 'the server is up' and 'it is partitioning tenants'."""
    status = await cognee.health()
    assert status.status == "healthy", (
        "either cognee is down, or it is running without ENABLE_BACKEND_ACCESS_CONTROL "
        "— in which case every memcp tenant shares one cognee account"
    )
    assert status.backend == "cognee"


async def test_a_memory_is_findable_the_moment_add_returns(cognee: CogneeBackend):
    """B5. Cognee's own `add()` ingests without extracting, and nothing is retrievable
    until a separate `cognify()` runs. The adapter takes the synchronous path instead,
    so there is no window in which `add_memory` has returned and the memory is not
    there. This test would be the one to fail if that ever changed — note that it does
    not retry, sleep or poll.
    """
    tenant = _tenant()
    content = "the immediate findability marker is a kingfisher behind the boiler room"
    try:
        await cognee.add(tenant, content, infer=False)
        hits = await cognee.search(tenant, "kingfisher boiler room", limit=10)
        assert any(m.content == content for m in hits), (
            f"add() returned before the memory was findable; got {[m.content for m in hits]}"
        )
    finally:
        await _wipe(cognee, tenant)


async def test_entities_come_from_a_graph_with_edges(cognee: CogneeBackend):
    """B1. Not a node with a count on it — nodes, and the relationships between them."""
    tenant = _tenant()
    try:
        await cognee.add(tenant, GRAPH_TEXT, infer=True)
        result = await cognee.entities(tenant)

        assert result.entities, "the graph held no entities for a tenant that has memories"
        assert result.relationships, (
            "the graph held entities but no relationships between them, which is the "
            "shape slice C removed from sqlite and in_memory for being a graph in name only"
        )
        identifiers = {e["id"] for e in result.entities}
        for relationship in result.relationships:
            assert relationship["source"] in identifiers
            assert relationship["target"] in identifiers
            assert relationship["relationship"], "an edge with no label is not a relationship"
    finally:
        await _wipe(cognee, tenant)


async def test_entities_do_not_cross_tenants(cognee: CogneeBackend):
    tenant, other = _tenant(), _tenant()
    try:
        await cognee.add(tenant, GRAPH_TEXT, infer=True)
        await cognee.add(other, "Grace Hopper built the COBOL compiler at Univac", infer=True)
        mine = await cognee.entities(tenant)
        names = {str(e.get("name", "")).lower() for e in mine.entities}
        assert not any("hopper" in n or "cobol" in n for n in names), (
            f"another tenant's entities appeared in this tenant's graph: {sorted(names)}"
        )
    finally:
        await _wipe(cognee, tenant, other)


async def test_identical_content_in_two_scopes_stays_two_memories(cognee: CogneeBackend):
    """Cognee deduplicates by content hash within a dataset. The per-memory header line
    in the uploaded file is what stops that collapsing two memories into one, and the
    round-trip corpus depends on it holding.
    """
    tenant = _tenant()
    content = "the same sentence filed under two different agents"
    try:
        await cognee.add(tenant, content, scope={"agent_id": "one"}, infer=False)
        await cognee.add(tenant, content, scope={"agent_id": "two"}, infer=False)
        listing = await cognee.list_memories(tenant, limit=100)
        matching = [m for m in listing.memories if m.content == content]
        assert len(matching) == 2, (
            f"cognee collapsed identical content in two scopes into {len(matching)} memory/ies"
        )
        assert {m.scope["agent_id"] for m in matching} == {"one", "two"}
    finally:
        await _wipe(cognee, tenant)
