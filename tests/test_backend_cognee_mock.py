"""CogneeBackend mock tests — the adapter's own logic, without a cognee server.

The conformance suite is what proves this adapter against a real cognee (CI stands one
up). What lives here is the behaviour a live run cannot easily produce on demand: an
expired token, a cognee configured without access control, a dataset holding data memcp
did not write, and the exact bytes that go on the wire.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from memcp.backend.cognee import HEADER_PREFIX, CogneeBackend
from memcp.types import MemoryAPIError

BASE = "https://cognee.test"
SECRET = "tenant-secret"
USER = "alice"
DATASET_ID = "11111111-1111-4111-8111-111111111111"
DATA_ID = "22222222-2222-4222-8222-222222222222"
MEMORY_ID = "abc123"


@pytest.fixture
async def backend():
    b = CogneeBackend(BASE, SECRET, email_domain="tenants.test.example")
    yield b
    await b.close()


def _name(memory_id: str, scope: dict, metadata: dict) -> str:
    envelope = json.dumps({"s": scope, "m": metadata}, separators=(",", ":"), sort_keys=True)
    packed = base64.urlsafe_b64encode(envelope.encode()).decode().rstrip("=")
    return f"{memory_id}~{packed}"


def _mock_login(token: str = "token-1") -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
    )


def _mock_dataset(rows: list[dict] | None = None) -> None:
    respx.get(f"{BASE}/api/v1/datasets").mock(
        return_value=httpx.Response(200, json=[{"id": DATASET_ID, "name": "memcp"}])
    )
    respx.get(f"{BASE}/api/v1/datasets/{DATASET_ID}/data").mock(
        return_value=httpx.Response(200, json=rows if rows is not None else [])
    )


def _row(name: str, data_id: str = DATA_ID) -> dict:
    return {
        "id": data_id,
        "name": name,
        "createdAt": "2026-08-17T00:00:00",
        "updatedAt": "2026-08-17T00:00:05",
    }


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def test_tenant_credentials_are_derived_and_stable():
    a = CogneeBackend(BASE, SECRET, email_domain="tenants.test.example")
    b = CogneeBackend(BASE, SECRET, email_domain="tenants.test.example")
    assert a._login(USER) == b._login(USER)
    assert a._login(USER) != a._login("bob")
    email, password = a._login(USER)
    assert email.endswith("@tenants.test.example")
    assert USER not in email, "the tenant id must not be readable off the derived login"
    assert len(password) == 64


def test_tenant_credentials_depend_on_the_secret():
    a = CogneeBackend(BASE, "one", email_domain="tenants.test.example")
    b = CogneeBackend(BASE, "two", email_domain="tenants.test.example")
    assert a._login(USER)[1] != b._login(USER)[1]


def test_empty_secret_is_refused():
    with pytest.raises(ValueError, match="COGNEE_TENANT_SECRET"):
        CogneeBackend(BASE, "")


@respx.mock
async def test_unknown_tenant_is_registered_then_logged_in(backend):
    login = respx.post(f"{BASE}/api/v1/auth/login").mock(
        side_effect=[
            httpx.Response(400, json={"detail": "LOGIN_BAD_CREDENTIALS"}),
            httpx.Response(200, json={"access_token": "token-1"}),
        ]
    )
    register = respx.post(f"{BASE}/api/v1/auth/register").mock(
        return_value=httpx.Response(201, json={"id": "u1"})
    )
    _mock_dataset([])
    await backend.list_memories(USER)
    assert register.called
    assert login.call_count == 2


@respx.mock
async def test_an_expired_token_is_replaced_once(backend):
    respx.post(f"{BASE}/api/v1/auth/login").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "stale"}),
            httpx.Response(200, json={"access_token": "fresh"}),
        ]
    )
    datasets = respx.get(f"{BASE}/api/v1/datasets").mock(
        side_effect=[
            httpx.Response(401, json={"detail": "Unauthorized"}),
            httpx.Response(200, json=[{"id": DATASET_ID, "name": "memcp"}]),
        ]
    )
    respx.get(f"{BASE}/api/v1/datasets/{DATASET_ID}/data").mock(
        return_value=httpx.Response(200, json=[])
    )
    await backend.list_memories(USER)
    assert datasets.call_count == 2
    assert datasets.calls[1].request.headers["authorization"] == "Bearer fresh"


# ---------------------------------------------------------------------------
# health: the isolation posture is part of it
# ---------------------------------------------------------------------------


@respx.mock
async def test_health_is_unhealthy_when_cognee_answers_unauthenticated_reads(backend):
    """A cognee without access control has one tenant, so memcp must not come up."""
    respx.get(f"{BASE}/health").mock(
        return_value=httpx.Response(200, json={"status": "ready", "health": "healthy"})
    )
    respx.get(f"{BASE}/api/v1/datasets").mock(return_value=httpx.Response(200, json=[]))
    status = await backend.health()
    assert status.status == "unhealthy"
    assert status.backend == "cognee"


@respx.mock
async def test_health_is_healthy_when_unauthenticated_reads_are_refused(backend):
    respx.get(f"{BASE}/health").mock(
        return_value=httpx.Response(200, json={"status": "ready", "health": "healthy"})
    )
    respx.get(f"{BASE}/api/v1/datasets").mock(return_value=httpx.Response(401))
    status = await backend.health()
    assert status.status == "healthy"


@respx.mock
async def test_health_is_unhealthy_when_the_server_is_down(backend):
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    assert (await backend.health()).status == "unhealthy"


# ---------------------------------------------------------------------------
# what goes on the wire
# ---------------------------------------------------------------------------


@respx.mock
async def test_add_uploads_an_envelope_and_a_header(backend):
    _mock_login()
    remember = respx.post(f"{BASE}/api/v1/remember").mock(
        return_value=httpx.Response(200, json={"status": "completed", "items": [{"id": DATA_ID}]})
    )
    results = await backend.add(
        USER, "a fact worth keeping", scope={"agent_id": "ferro"}, metadata={"source": "test"}
    )
    assert len(results) == 1

    body = remember.calls[0].request.content.decode("utf-8", "replace")
    assert 'name="datasetName"' in body and "memcp" in body
    assert 'name="run_in_background"' in body and "false" in body, (
        "a background cognify would return before the memory is findable"
    )
    assert HEADER_PREFIX + results[0].id in body, (
        "the per-memory header is what stops cognee collapsing identical content"
    )
    assert "a fact worth keeping" in body
    filename = body.split('filename="')[1].split('"')[0]
    assert filename.startswith(results[0].id + "~")
    envelope = filename.removesuffix(".txt").split("~", 1)[1]
    decoded = json.loads(base64.urlsafe_b64decode(envelope + "=" * (-len(envelope) % 4)))
    assert decoded == {"s": {"agent_id": "ferro"}, "m": {"source": "test"}}


@respx.mock
async def test_add_refuses_to_claim_a_write_cognee_did_not_finish(backend):
    _mock_login()
    respx.post(f"{BASE}/api/v1/remember").mock(
        return_value=httpx.Response(200, json={"status": "running", "items": []})
    )
    with pytest.raises(MemoryAPIError) as exc:
        await backend.add(USER, "a fact")
    assert "findable" in str(exc.value)


@respx.mock
async def test_add_rejects_nested_filters(backend):
    with pytest.raises(ValueError):
        await backend.add(USER, "x", scope={"OR": [{"agent_id": "a"}]})


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_reads_content_scope_and_metadata_back(backend):
    _mock_login()
    _mock_dataset([_row(_name(MEMORY_ID, {"agent_id": "ferro"}, {"source": "test"}))])
    respx.get(f"{BASE}/api/v1/datasets/{DATASET_ID}/data/{DATA_ID}/raw").mock(
        return_value=httpx.Response(200, text=f"{HEADER_PREFIX}{MEMORY_ID}\n\nthe stored content")
    )
    memory = await backend.get(USER, MEMORY_ID)
    assert memory is not None
    assert memory.id == MEMORY_ID
    assert memory.content == "the stored content"
    assert memory.scope == {"agent_id": "ferro"}
    assert memory.metadata == {"source": "test"}


@respx.mock
async def test_data_memcp_did_not_write_is_ignored(backend):
    """A cognee dataset can hold someone else's upload. It is not a memory."""
    _mock_login()
    _mock_dataset(
        [
            _row("a-plain-filename-nobody-encoded", data_id=DATA_ID),
            _row(_name(MEMORY_ID, {}, {}), data_id="33333333-3333-4333-8333-333333333333"),
        ]
    )
    respx.get(
        f"{BASE}/api/v1/datasets/{DATASET_ID}/data/33333333-3333-4333-8333-333333333333/raw"
    ).mock(return_value=httpx.Response(200, text=f"{HEADER_PREFIX}{MEMORY_ID}\n\nmine"))
    result = await backend.list_memories(USER)
    assert [m.id for m in result.memories] == [MEMORY_ID]


@respx.mock
async def test_search_returns_nothing_when_the_tenant_has_no_dataset(backend):
    _mock_login()
    respx.get(f"{BASE}/api/v1/datasets").mock(return_value=httpx.Response(200, json=[]))
    assert await backend.search(USER, "anything") == []


@respx.mock
async def test_search_survives_a_dataset_that_was_never_cognified(backend):
    _mock_login()
    _mock_dataset([_row(_name(MEMORY_ID, {}, {}))])
    respx.post(f"{BASE}/api/v1/recall").mock(
        return_value=httpx.Response(404, json={"detail": "No datasets found."})
    )
    assert await backend.search(USER, "anything") == []


@respx.mock
async def test_search_filters_by_scope_after_ranking(backend):
    _mock_login()
    other_data_id = "33333333-3333-4333-8333-333333333333"
    _mock_dataset(
        [
            _row(_name("wanted", {"agent_id": "keep"}, {}), data_id=DATA_ID),
            _row(_name("unwanted", {"agent_id": "drop"}, {}), data_id=other_data_id),
        ]
    )
    respx.post(f"{BASE}/api/v1/recall").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"text": "b", "metadata": {"data_id": other_data_id}},
                {"text": "a", "metadata": {"data_id": DATA_ID}},
            ],
        )
    )
    respx.get(f"{BASE}/api/v1/datasets/{DATASET_ID}/data/{DATA_ID}/raw").mock(
        return_value=httpx.Response(200, text=f"{HEADER_PREFIX}wanted\n\nkept content")
    )
    hits = await backend.search(USER, "query", scope={"agent_id": "keep"})
    assert [m.id for m in hits] == ["wanted"]
    assert hits[0].content == "kept content"
    assert hits[0].score is None, "cognee's CHUNKS recall reports no score"


# ---------------------------------------------------------------------------
# writes that have to fail
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_missing_raises_404(backend):
    _mock_login()
    _mock_dataset([])
    with pytest.raises(MemoryAPIError) as exc:
        await backend.delete(USER, MEMORY_ID)
    assert exc.value.status == 404


@respx.mock
async def test_delete_all_only_removes_the_requested_scope(backend):
    _mock_login()
    other_data_id = "33333333-3333-4333-8333-333333333333"
    _mock_dataset(
        [
            _row(_name("doomed", {"agent_id": "drop"}, {}), data_id=DATA_ID),
            _row(_name("spared", {"agent_id": "keep"}, {}), data_id=other_data_id),
        ]
    )
    deleted = respx.delete(f"{BASE}/api/v1/datasets/{DATASET_ID}/data/{DATA_ID}").mock(
        return_value=httpx.Response(200)
    )
    count = await backend.delete_all(USER, {"agent_id": "drop"})
    assert count == 1
    assert deleted.called


@respx.mock
async def test_update_of_a_missing_memory_does_not_delete_anything(backend):
    _mock_login()
    _mock_dataset([])
    deleted = respx.delete(url__regex=rf"{BASE}/api/v1/datasets/.*").mock(
        return_value=httpx.Response(200)
    )
    with pytest.raises(MemoryAPIError) as exc:
        await backend.update(USER, MEMORY_ID, "replacement")
    assert exc.value.status == 404
    assert not deleted.called


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


@respx.mock
async def test_entities_drops_pipeline_nodes_and_keeps_relationships(backend):
    _mock_login()
    respx.get(f"{BASE}/api/v1/datasets").mock(
        return_value=httpx.Response(200, json=[{"id": DATASET_ID, "name": "memcp"}])
    )
    respx.get(f"{BASE}/api/v1/datasets/{DATASET_ID}/graph").mock(
        return_value=httpx.Response(
            200,
            json={
                "nodes": [
                    {"id": "chunk", "label": "DocumentChunk_x", "type": "DocumentChunk"},
                    {"id": "doc", "label": "TextDocument_x", "type": "TextDocument"},
                    {
                        "id": "ada",
                        "label": "ada lovelace",
                        "type": "Entity",
                        "properties": {"description": "Ada Lovelace"},
                    },
                    {"id": "engine", "label": "analytical engine", "type": "Entity"},
                ],
                "edges": [
                    {"source": "ada", "target": "engine", "label": "worked_on"},
                    {"source": "chunk", "target": "doc", "label": "is_part_of"},
                ],
            },
        )
    )
    result = await backend.entities(USER)
    assert [e["id"] for e in result.entities] == ["ada", "engine"]
    assert result.entities[0]["name"] == "ada lovelace"
    assert result.relationships == [
        {"source": "ada", "target": "engine", "relationship": "worked_on"}
    ]


@respx.mock
async def test_entities_are_empty_for_a_tenant_with_no_dataset(backend):
    _mock_login()
    respx.get(f"{BASE}/api/v1/datasets").mock(return_value=httpx.Response(200, json=[]))
    result = await backend.entities(USER)
    assert result.entities == []
    assert result.relationships == []
