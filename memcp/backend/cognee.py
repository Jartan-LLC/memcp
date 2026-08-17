"""Cognee REST API adapter — the first backend with a real knowledge graph.

Cognee is dataset-oriented and graph-shaped where memcp's other backends are flat,
so this adapter is where the Protocol either holds or does not. Four decisions carry
the whole thing, and each of them came out of a measurement against a running cognee
rather than from its documentation:

**A memcp tenant is a cognee *user*, not a cognee dataset.** Cognee's `datasets`
parameter selects which dataset a recall result is *attributed* to, not which data it
is drawn from: with `ENABLE_BACKEND_ACCESS_CONTROL=false`, asking for dataset B
returns dataset A's chunks stamped with B's id, and `GET /datasets/{id}/graph` returns
the same nodes whichever dataset is named. With access control on, per-user
partitioning is real — recall, dataset listing and the graph endpoint each return only
the authenticated user's own data. So tenancy rides on the mechanism cognee actually
enforces — and `health()` refuses to report healthy against a server configured the
other way, because a cognee that answers unauthenticated requests has one tenant.

**Credentials are derived, not stored.** memcp keeps no state of its own, so a per
tenant cognee account is minted deterministically: the login is
`t-<sha256(tenant)>@<domain>` and the password is `HMAC-SHA256(secret, tenant)`. Any
memcp process holding the same `COGNEE_TENANT_SECRET` re-derives the same credential
and needs no shared database to do it. The secret is the tenant boundary; it is minted
by `memcp up` and lives only in the deployment's `.env`.

**A memory is one uploaded file, and its envelope is the filename.** `id~<base64url
json>` carries the memcp memory id, its scope and its metadata; cognee stores the
basename verbatim and hands it back on both the data listing and every recall hit.
Content is the file body, prefixed with one `memcp-id:` header line — cognee
deduplicates by content hash within a dataset, so two memories with identical content
in two different scopes collapse into one row without it. That is not hypothetical: it
is what the round-trip corpus writes.

**`add()` returns only once the memory is findable.** `POST /v1/remember` with
`run_in_background=false` runs ingestion and the cognify graph build in the request,
so the write is complete when the call returns. `POST /v1/add` alone would return
faster and leave nothing retrievable until a separate `cognify()` ran — the failure
mode the Protocol has never had to name, because every other backend is synchronous.
memcp does not take that trade: `add_memory` promises findability.

What this adapter does not do: honour `threshold`. Cognee's CHUNKS recall returns no
score, so there is nothing to threshold against and `Memory.score` is None on every
result. Ranking still happens inside cognee; it is simply not reported.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from memcp.types import (
    AddResult,
    EntitiesResult,
    HealthStatus,
    ListResult,
    Memory,
    MemoryAPIError,
    paginate,
    reject_nested_filters,
)

from .base import MemoryBackend

logger = logging.getLogger(__name__)

# The dataset every memcp memory for a tenant is written into. One per tenant account,
# because the tenant boundary is the account — see the module docstring.
DEFAULT_DATASET = "memcp"

# Where derived tenant logins live. Cognee validates the address, and every
# special-use suffix (.local, .invalid, .test, example.com) is refused, so this has to
# be a syntactically ordinary domain. Nothing is ever sent to it.
DEFAULT_EMAIL_DOMAIN = "tenants.memcp.internal.jartan.dev"

# Separates the memory id from its base64url envelope in the uploaded filename.
NAME_SEPARATOR = "~"

# First line of every uploaded file. Defeats cognee's content-hash deduplication,
# which would otherwise collapse identical content written into two different scopes.
HEADER_PREFIX = "memcp-id: "

# Graph node types cognee creates to represent the document pipeline rather than
# anything extracted from the text. `entities()` reports what the graph knows, not how
# the graph was built.
INFRASTRUCTURE_NODE_TYPES = frozenset({"DocumentChunk", "TextDocument", "TextSummary"})

# How many extra recall hits to ask for when the caller narrowed by scope. Cognee
# cannot filter on the envelope, so scope is applied here, after ranking — asking for
# exactly `limit` would return fewer than `limit` matches whenever anything in the
# tenant's other scopes ranked higher.
SCOPE_FANOUT = 5
MAX_TOP_K = 200


class CogneeBackend(MemoryBackend):
    """Adapter for a cognee server's REST API."""

    # Cognee stores the submitted text verbatim and builds a graph *beside* it, so
    # add(infer=True) never replaces content with extracted facts the way mem0 does.
    # The graph it extracts is reachable through memory_entities, not through content.
    extracts_facts = False

    # Retrieval is embedding similarity over cognee's vector store, which is a
    # different question from whether content survives a write.
    retrieval = "semantic"

    def __init__(
        self,
        base_url: str,
        tenant_secret: str,
        *,
        dataset: str = DEFAULT_DATASET,
        email_domain: str = DEFAULT_EMAIL_DOMAIN,
        timeout: float = 120.0,
    ) -> None:
        if not tenant_secret:
            raise ValueError(
                "COGNEE_TENANT_SECRET is required: it derives every tenant's cognee "
                "credential, so an empty secret would give every tenant the same account."
            )
        self._secret = tenant_secret.encode("utf-8")
        self._dataset = dataset
        self._domain = email_domain
        # A cognify call runs an LLM over the submitted text inside the request, which
        # is why the default timeout is minutes rather than seconds.
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
        self._tokens: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # --- credentials -------------------------------------------------------

    def _login(self, user_id: str) -> tuple[str, str]:
        """The cognee account for a memcp tenant, derived rather than looked up."""
        handle = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
        password = hmac.new(self._secret, user_id.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"t-{handle}@{self._domain}", password

    def _lock(self, user_id: str) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = self._locks[user_id] = asyncio.Lock()
        return lock

    async def _token(self, user_id: str, *, refresh: bool = False) -> str:
        cached = self._tokens.get(user_id)
        if cached and not refresh:
            return cached
        async with self._lock(user_id):
            cached = self._tokens.get(user_id)
            if cached and not refresh:
                return cached
            email, password = self._login(user_id)
            token = await self._authenticate(email, password)
            self._tokens[user_id] = token
            return token

    async def _authenticate(self, email: str, password: str) -> str:
        form = {"username": email, "password": password}
        resp = await self._http.post("/api/v1/auth/login", data=form)
        if resp.status_code == 400:
            # First time this tenant has been seen by this cognee server.
            created = await self._http.post(
                "/api/v1/auth/register", json={"email": email, "password": password}
            )
            if created.status_code >= 400 and created.status_code != 400:
                raise MemoryAPIError(created.status_code, created.text)
            resp = await self._http.post("/api/v1/auth/login", data=form)
        if resp.status_code >= 400:
            raise MemoryAPIError(resp.status_code, resp.text)
        token = resp.json().get("access_token")
        if not token:
            raise MemoryAPIError(502, "cognee login returned no access_token")
        return str(token)

    async def _request(
        self,
        user_id: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        allow: tuple[int, ...] = (),
    ) -> httpx.Response:
        """One authenticated call, re-authenticating once if the token has expired.

        `allow` lists non-2xx statuses the caller interprets itself; everything else
        becomes a MemoryAPIError.
        """
        for attempt in (1, 2):
            token = await self._token(user_id, refresh=attempt == 2)
            headers = {"Authorization": f"Bearer {token}"}
            try:
                resp = await self._http.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=headers,
                )
            except httpx.TimeoutException as e:
                raise MemoryAPIError(408, f"Timeout: {e}") from e
            except httpx.RequestError as e:
                raise MemoryAPIError(503, f"Network error: {e}") from e
            if resp.status_code == 401 and attempt == 1:
                continue
            if resp.status_code >= 400 and resp.status_code not in allow:
                raise MemoryAPIError(resp.status_code, resp.text)
            return resp
        raise MemoryAPIError(401, "cognee rejected a freshly minted token")

    # --- envelope ----------------------------------------------------------

    @staticmethod
    def _encode_name(memory_id: str, scope: dict[str, Any], metadata: dict[str, Any]) -> str:
        envelope = json.dumps({"s": scope, "m": metadata}, separators=(",", ":"), sort_keys=True)
        packed = base64.urlsafe_b64encode(envelope.encode("utf-8")).decode("ascii").rstrip("=")
        return f"{memory_id}{NAME_SEPARATOR}{packed}"

    @staticmethod
    def _decode_name(name: str) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        """(id, scope, metadata) for a name this adapter wrote, else None.

        A cognee dataset can hold data nobody put there through memcp — someone's own
        upload, or a file added before memcp was pointed at the server. Those are not
        memcp memories and are skipped rather than guessed at.
        """
        memory_id, separator, packed = name.partition(NAME_SEPARATOR)
        if not separator or not memory_id:
            return None
        padded = packed + "=" * (-len(packed) % 4)
        try:
            envelope = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except Exception:
            return None
        if not isinstance(envelope, dict):
            return None
        scope = envelope.get("s") or {}
        metadata = envelope.get("m") or {}
        if not isinstance(scope, dict) or not isinstance(metadata, dict):
            return None
        return memory_id, scope, metadata

    @staticmethod
    def _encode_body(memory_id: str, content: str) -> bytes:
        return f"{HEADER_PREFIX}{memory_id}\n\n{content}".encode()

    @staticmethod
    def _decode_body(raw: str) -> str:
        if raw.startswith(HEADER_PREFIX):
            _, _, rest = raw.partition("\n\n")
            return rest
        return raw

    # --- dataset plumbing --------------------------------------------------

    async def _dataset_id(self, user_id: str) -> str | None:
        resp = await self._request(user_id, "GET", "/api/v1/datasets")
        for entry in resp.json():
            if entry.get("name") == self._dataset:
                return str(entry["id"])
        return None

    async def _rows(self, user_id: str) -> tuple[str | None, list[dict[str, Any]]]:
        """Every memcp memory in the tenant's dataset, newest last, without content."""
        dataset_id = await self._dataset_id(user_id)
        if dataset_id is None:
            return None, []
        resp = await self._request(
            user_id, "GET", f"/api/v1/datasets/{dataset_id}/data", allow=(403, 404)
        )
        if resp.status_code >= 400:
            return dataset_id, []
        rows: list[dict[str, Any]] = []
        for entry in resp.json():
            decoded = self._decode_name(str(entry.get("name", "")))
            if decoded is None:
                continue
            memory_id, scope, metadata = decoded
            rows.append(
                {
                    "memory_id": memory_id,
                    "data_id": str(entry["id"]),
                    "scope": scope,
                    "metadata": metadata,
                    "created_at": entry.get("createdAt") or "",
                    "updated_at": entry.get("updatedAt"),
                }
            )
        rows.sort(key=lambda r: (r["created_at"], r["memory_id"]))
        return dataset_id, rows

    async def _content(self, user_id: str, dataset_id: str, data_id: str) -> str | None:
        resp = await self._request(
            user_id,
            "GET",
            f"/api/v1/datasets/{dataset_id}/data/{data_id}/raw",
            allow=(401, 403, 404),
        )
        if resp.status_code >= 400:
            return None
        return self._decode_body(resp.text)

    async def _materialize(
        self, user_id: str, dataset_id: str, rows: list[dict[str, Any]]
    ) -> list[Memory]:
        """Attach content to rows. One request per memory — cognee has no bulk read."""
        contents = await asyncio.gather(
            *(self._content(user_id, dataset_id, row["data_id"]) for row in rows)
        )
        out: list[Memory] = []
        for row, content in zip(rows, contents, strict=True):
            if content is None:
                logger.warning("cognee data %s vanished between listing and read", row["data_id"])
                continue
            out.append(
                Memory(
                    id=row["memory_id"],
                    content=content,
                    score=None,
                    scope=dict(row["scope"]),
                    metadata=dict(row["metadata"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return out

    # --- required ----------------------------------------------------------

    async def add(
        self,
        user_id: str,
        content: str,
        *,
        scope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> list[AddResult]:
        """Ingest and build the graph in one blocking call.

        `infer` selects nothing here. Cognee has no verbatim-only ingest that leaves a
        memory findable — `POST /v1/add` without cognify stores bytes that no recall
        returns — so both settings run the same pipeline, and content survives either
        way because cognee keeps the chunk text alongside the graph it extracts.
        """
        if scope:
            reject_nested_filters(scope)
        memory_id = hashlib.sha256(f"{user_id}:{time.time_ns()}:{content}".encode()).hexdigest()[
            :32
        ]
        name = self._encode_name(memory_id, dict(scope or {}), dict(metadata or {}))

        resp = await self._request(
            user_id,
            "POST",
            "/api/v1/remember",
            files={"data": (f"{name}.txt", self._encode_body(memory_id, content), "text/plain")},
            data={
                "datasetName": self._dataset,
                "run_in_background": "false",
            },
        )
        body = resp.json() if resp.content else {}
        status = body.get("status") if isinstance(body, dict) else None
        if status != "completed":
            raise MemoryAPIError(
                502,
                f"cognee remember returned status {status!r}; the memory is not "
                f"guaranteed to be findable: {json.dumps(body)[:400]}",
            )
        return [AddResult(id=memory_id, status="ready", created_at="")]

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[Memory]:
        if scope:
            reject_nested_filters(scope)
        dataset_id, rows = await self._rows(user_id)
        if dataset_id is None or not rows:
            return []
        by_data_id = {row["data_id"]: row for row in rows}

        top_k = min(limit * SCOPE_FANOUT, MAX_TOP_K) if scope else limit
        resp = await self._request(
            user_id,
            "POST",
            "/api/v1/recall",
            json_body={
                "query": query,
                "search_type": "CHUNKS",
                "top_k": top_k,
                "datasets": [self._dataset],
            },
            allow=(404, 422),
        )
        if resp.status_code >= 400:
            # A tenant whose dataset has never been cognified answers 404 here. That is
            # an empty result, not a failure.
            return []

        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in resp.json():
            data_id = str((hit.get("metadata") or {}).get("data_id") or "")
            row = by_data_id.get(data_id)
            if row is None or data_id in seen:
                continue
            if scope and not _scope_matches(row["scope"], scope):
                continue
            seen.add(data_id)
            ordered.append(row)
            if len(ordered) >= limit:
                break
        return await self._materialize(user_id, dataset_id, ordered)

    async def delete(self, user_id: str, memory_id: str) -> bool:
        dataset_id, rows = await self._rows(user_id)
        row = next((r for r in rows if r["memory_id"] == memory_id), None)
        if dataset_id is None or row is None:
            # Also the cross-tenant answer: another tenant's memory is not in this
            # tenant's dataset, and cognee's own refusal is a 401 we never reach.
            raise MemoryAPIError(404, "Not found")
        await self._delete_data(user_id, dataset_id, row["data_id"])
        return True

    async def _delete_data(self, user_id: str, dataset_id: str, data_id: str) -> None:
        await self._request(
            user_id, "DELETE", f"/api/v1/datasets/{dataset_id}/data/{data_id}", allow=(404,)
        )

    async def delete_all(self, user_id: str, scope: dict[str, Any]) -> int:
        reject_nested_filters(scope)
        dataset_id, rows = await self._rows(user_id)
        if dataset_id is None:
            return 0
        doomed = [r for r in rows if _scope_matches(r["scope"], scope)]
        for row in doomed:
            await self._delete_data(user_id, dataset_id, row["data_id"])
        return len(doomed)

    async def health(self) -> HealthStatus:
        """Is the server up, and is it partitioning tenants?

        The second half is not decoration. memcp's whole tenant boundary on this
        backend is cognee's per-user access control; a cognee started with
        `ENABLE_BACKEND_ACCESS_CONTROL=false` serves every request as one default user,
        and every memcp tenant would read and write the same memories. There is no
        endpoint that reports that posture, but there is a behaviour that gives it
        away: with access control on, an unauthenticated read is refused. So this asks.

        Reporting unhealthy is what makes it matter — `memcp up --wait` gates on
        /health, so a cognee stood up in single-tenant mode fails provisioning instead
        of quietly serving fifteen agents out of one bucket.
        """
        start = time.monotonic()
        try:
            resp = await self._http.get("/health")
            latency = round((time.monotonic() - start) * 1000, 1)
            if resp.status_code != 200 or (resp.json() or {}).get("health") != "healthy":
                return HealthStatus(status="unhealthy", backend="cognee", latency_ms=latency)
            if not await self._isolation_enforced():
                logger.error(
                    "cognee served an unauthenticated read of /api/v1/datasets, so it is "
                    "running without ENABLE_BACKEND_ACCESS_CONTROL and every memcp tenant "
                    "would share one cognee account. Refusing to report healthy."
                )
                return HealthStatus(status="unhealthy", backend="cognee", latency_ms=latency)
            return HealthStatus(status="healthy", backend="cognee", latency_ms=latency)
        except Exception:
            logger.warning("cognee health check failed", exc_info=True)
            latency = round((time.monotonic() - start) * 1000, 1)
            return HealthStatus(status="unhealthy", backend="cognee", latency_ms=latency)

    async def _isolation_enforced(self) -> bool:
        """True when cognee refuses an unauthenticated read."""
        resp = await self._http.get("/api/v1/datasets")
        return resp.status_code == 401

    def capabilities(self) -> set[str]:
        # No memory_history: cognee keeps a pipeline run log, not a per-memory change
        # log, and nothing in its API answers "what did this memory say before".
        return {
            "get_memory",
            "update_memory",
            "list_memories",
            "memory_entities",
        }

    def scope_keys(self) -> list[str]:
        return ["agent_id", "run_id"]

    # --- optional ----------------------------------------------------------

    async def get(self, user_id: str, memory_id: str) -> Memory | None:
        dataset_id, rows = await self._rows(user_id)
        row = next((r for r in rows if r["memory_id"] == memory_id), None)
        if dataset_id is None or row is None:
            return None
        found = await self._materialize(user_id, dataset_id, [row])
        return found[0] if found else None

    async def update(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """Replace a memory's content, keeping its id.

        Cognee has no in-place edit that re-runs extraction over the new text, so this
        deletes the old data item and writes a new one under the same memcp id. It is
        not atomic: a failure between the two leaves the memory deleted. The id, scope
        and (unless replaced) metadata survive; `created_at` does not, because the new
        item is genuinely new to cognee.
        """
        dataset_id, rows = await self._rows(user_id)
        row = next((r for r in rows if r["memory_id"] == memory_id), None)
        if dataset_id is None or row is None:
            raise MemoryAPIError(404, "Not found")

        new_metadata = dict(metadata) if metadata is not None else dict(row["metadata"])
        await self._delete_data(user_id, dataset_id, row["data_id"])
        name = self._encode_name(memory_id, dict(row["scope"]), new_metadata)
        resp = await self._request(
            user_id,
            "POST",
            "/api/v1/remember",
            files={"data": (f"{name}.txt", self._encode_body(memory_id, content), "text/plain")},
            data={"datasetName": self._dataset, "run_in_background": "false"},
        )
        body = resp.json() if resp.content else {}
        if (body or {}).get("status") != "completed":
            raise MemoryAPIError(
                503, f"update deleted the old memory but the rewrite did not complete: {body}"
            )
        rewritten = await self.get(user_id, memory_id)
        if rewritten is None:
            raise MemoryAPIError(503, "Update succeeded but read-back failed")
        return rewritten

    async def list_memories(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ListResult:
        if scope:
            reject_nested_filters(scope)
        dataset_id, rows = await self._rows(user_id)
        if dataset_id is None:
            return ListResult()
        if scope:
            rows = [r for r in rows if _scope_matches(r["scope"], scope)]
        # Paginate over the cheap listing, then read content for the page only.
        page = paginate([_placeholder(r) for r in rows], cursor, limit)
        wanted = {m.id for m in page.memories}
        selected = [r for r in rows if r["memory_id"] in wanted]
        return ListResult(
            memories=await self._materialize(user_id, dataset_id, selected),
            next_cursor=page.next_cursor,
        )

    async def entities(
        self,
        user_id: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> EntitiesResult:
        """The tenant's knowledge graph, as cognee built it.

        Nodes cognee creates to represent the ingest pipeline itself — the document,
        its chunks, its summaries — are not entities and are dropped. What is left is
        what extraction found in the text, and the edges between those nodes are real
        relationships rather than a count dressed up as one.
        """
        dataset_id = await self._dataset_id(user_id)
        if dataset_id is None:
            return EntitiesResult()
        resp = await self._request(
            user_id, "GET", f"/api/v1/datasets/{dataset_id}/graph", allow=(403, 404, 500)
        )
        if resp.status_code >= 400:
            return EntitiesResult()
        body = resp.json() or {}

        entities: list[dict[str, Any]] = []
        kept: set[str] = set()
        for node in body.get("nodes", []):
            if node.get("type") in INFRASTRUCTURE_NODE_TYPES:
                continue
            node_id = str(node.get("id", ""))
            properties = node.get("properties") or {}
            kept.add(node_id)
            entities.append(
                {
                    "id": node_id,
                    "name": node.get("label") or properties.get("name") or "",
                    "type": node.get("type") or "Entity",
                    "description": properties.get("description", ""),
                }
            )

        relationships = [
            {
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "relationship": edge.get("label") or "",
            }
            for edge in body.get("edges", [])
            if str(edge.get("source", "")) in kept and str(edge.get("target", "")) in kept
        ]
        return EntitiesResult(entities=entities[:limit], relationships=relationships)

    # --- lifecycle ---------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()


def _placeholder(row: dict[str, Any]) -> Memory:
    """A row's identity without its content, so pagination costs no reads."""
    return Memory(id=row["memory_id"], content="", scope=dict(row["scope"]))


def _scope_matches(stored: dict[str, Any], wanted: dict[str, Any]) -> bool:
    return all(stored.get(k) == v for k, v in wanted.items())
