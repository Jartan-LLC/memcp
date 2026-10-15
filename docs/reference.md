# Reference

Environment variables, the MCP tool surface, and what memcp does not do. Consulted
mid-task rather than read through. For getting a deployment running, see
[deployment.md](deployment.md).

## Requirements

- Docker, for `docker-compose.yml`
- Python 3.12+, to run the server directly
- For `MEMCP_BACKEND=mem0`: a running [mem0](https://github.com/mem0ai/mem0) instance

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MEMCP_BACKEND` | Yes | Backend: `mem0`, `sqlite` or `in_memory`. memcp refuses to start without it |
| `MEMCP_SQLITE_PATH` | No | Database file for the sqlite backend (default: `memcp.sqlite3`) |
| `MEM0_API_BASE` | mem0 | Base URL of your mem0 REST API |
| `MEM0_API_KEY` | mem0 | API key for the mem0 server |
| `MEMCP_AUTH_TOKENS` | No | Token-to-tenant mapping: `tok1:alice,tok2:bob`. Parsed exactly as before this project added attribution — a `user_id` containing a colon still round-trips unchanged. Unset means unauthenticated, which is refused on any non-loopback bind |
| `MEMCP_AUTH_SEATS` | No | Optional token-to-seat mapping, layered onto `MEMCP_AUTH_TOKENS`: `tok1:agent-one,tok2:agent-two`, seat matching `[A-Za-z0-9_.-]+`. What memcp stamps as `author` on every write that token makes. A token named here with no `MEMCP_AUTH_TOKENS` entry is rejected at startup. Two tokens can share a tenant with different seats — that is how one shared token becomes individually attributable — and a token absent from this mapping keeps its seat equal to its tenant, as it always has |
| `MEMCP_HOST` | No | Bind address (default: `0.0.0.0`) |
| `MEMCP_PORT` | No | Bind port (default: `8080`) |
| `MEMCP_ALLOWED_HOSTS` | No | Host header allow-list for DNS-rebinding protection, comma-separated, `:*` for any port. `docker-compose.yml` defaults it to `127.0.0.1:*,localhost:*`; a value replaces that. Without Docker, unset leaves the MCP SDK's rule, which is off unless `MEMCP_HOST` is loopback |
| `MEMCP_LOG_LEVEL` | No | Log level (default: `INFO`) |
| `MEMCP_LOG_FORMAT` | No | Log format: `json` or `plain` (default: `json`) |

`.env.example` carries the same set in the shape a deployment reads it.

## MCP tools

Names, argument schemas and behaviour annotations are frozen in
[tool-surface.json](tool-surface.json); a test fails on any drift.

Every memory a read tool returns carries `author` (the seat `MEMCP_AUTH_TOKENS`
resolved at write time, server-stamped — a caller cannot set it) and `attributed`
(`false` when `author` is `null`, which is every row written before this field
existed). It is a record of what some client stored, not a verified fact — see
the server's own MCP instructions for how a client is told to treat it.

**`attributed: true` only means a server version that validates `metadata`
produced this stamp — it is not a statement about a store's entire history.**
Before this field existed, `metadata` was entirely unvalidated, so any caller
holding a bearer token could have written the reserved key directly, and a row
like that would read back exactly like a real stamp. `sqlite` and `in_memory`
close this **for a file only ever served by a version carrying this guard**: a
one-time, marked cleanse strips the reserved namespace from every row already
in the file the first time such a version opens it, and the write path itself
now refuses a caller-supplied reserved key regardless of caller. The marker
does not re-trigger — a file that is later opened by an unguarded version
(a rollback, then a roll-forward) can have a key planted in that window, and
the guarded version that reopens it afterward will not know to look again.
`mem0` has no local file this server can scan and no way to enumerate every
tenant an install holds — **run a metadata sweep on a `mem0`-backed store
before serving it with a server version carrying this guard**, or a
pre-existing row will read as attributed when it never was.

### Scope

A scope labels memories inside a tenant; `memory_status` lists the keys the backend
supports (`agent_id` and `run_id` on every bundled backend). Every tool that takes a scope
applies the same rules:

- `user_id` is refused; the server sets it from your token.
- Empty, `*` and `null` values are refused.
- `agent_id` is normalized: lowercased, trimmed, every run of other characters turned into
  one hyphen, and hyphens at either end dropped.
- An `agent_id` with no letters or digits is refused.
- Any other value with whitespace at either end is refused, and so is a `run_id` with
  whitespace inside it.
- A number that isn't finite (NaN or infinity) is refused.

Search and list leave out a memory the backend returns outside the requested scope, and log
a warning (`memcp.policy`).

### Universal — registered on every backend

| Tool | Description |
|---|---|
| `add_memory` | Store a fact/preference/decision. On `mem0`, extracts facts by default (may store nothing) and `infer=false` stores verbatim. On `sqlite` and `in_memory` there is no model, so content is always stored verbatim and `infer` has no effect — the tool's own description says which, per deployment. Bulk: use `import_memories` |
| `search_memory` | Ranked search — semantic on `mem0`, keyword on `sqlite` and `in_memory`. `threshold` filters by minimum score (0-1). For browsing: `list_memories` |
| `delete_memory` | Delete one memory by ID. Confirm with user first |
| `delete_all_memories` | Bulk-delete by scope (e.g. agent_id, run_id), not content. Requires at least one scope key, each with a concrete value (not empty, `*` or null). Confirm first |
| `memory_status` | Returns server version, backend type, capabilities, valid scope keys, whether the backend extracts facts, and whether retrieval is semantic or keyword. No memory content |

### Optional — registered only by a backend that implements them

| Tool | Description |
|---|---|
| `get_memory` | Fetch one memory by ID. Returns full content, scope, and metadata |
| `update_memory` | Full-replace a memory's content (not a patch). Scope immutable — to change scope, add new + delete old |
| `list_memories` | Browse memories, optionally filtered by scope. Unranked, paginated; `incomplete: true` means memories may exist beyond the backend's list ceiling. For semantic queries: `search_memory` |
| `export_memories` | Export memories as JSON (max 10k; `truncated: true` when it stops there or the backend's list may be incomplete). For backup/migration. Output compatible with `import_memories` (requires `list_memories`) |
| `import_memories` | Batch-import from JSON. Dedup via exact content match, scope-aware; `dedup_incomplete: true` means dedup may have missed existing memories, so duplicates are possible. `on_conflict`: skip, overwrite, duplicate (requires `list_memories`; overwrite requires `update_memory`) |
| `memory_history` | Change log for a memory: timestamps and previous/current content per create/update event |
| `memory_entities` | Knowledge graph: entities and relationships. Registered only by a backend that has one — `mem0` does, `sqlite` and `in_memory` do not, so it is absent on a keyless install. `incomplete: true` means the counts may miss some memories. Not a search tool — use `search_memory` for topics |

### Errors

A tool that fails returns an error object instead of its result:

```json
{"error": {"code": "scope_required", "message": "delete_all_memories requires at least one scope key.", "retry": false}}
```

Branch on `code`; `message` is for people. `retry` is `true` only when the same call may
succeed later.

| Code | When | `retry` |
|---|---|---|
| `validation_error` | An argument is malformed or out of range | `false` |
| `invalid_scope` | The scope names a key the backend doesn't support | `false` |
| `nested_filter` | The scope uses `AND`, `OR` or `NOT` | `false` |
| `scope_required` | `delete_all_memories` got an empty scope | `false` |
| `not_found` | The memory doesn't exist or belongs to another tenant | `false` |
| `not_supported` | `import_memories` with `on_conflict=overwrite` on a backend without `update_memory` | `false` |
| `timeout` | The backend didn't answer in time | `true` |
| `backend_error` | The backend returned an error | `true` for a 5xx, otherwise `false` |

A rejected entry doesn't fail `import_memories`: it imports the entries it can and lists
each rejected one in its result's `errors`, with the entry's `index` and the reason in
`error`.

A request without a valid bearer token gets HTTP 401 with the same object and the code
`unauthorized`.

## Known limitations

**mem0 backend — upstream constraints:**

- Nested boolean filters (AND/OR/NOT) return 502 — use flat scope keys
- List endpoint does not paginate server-side — up to 1000 memories are loaded per request, and memcp pages them
- List returns at most 1000 memories (mem0's own ceiling) and can't page past it, so a tenant at or above 1000 memories is reported as incomplete (see each tool's flag above)
- List endpoint does not filter by metadata
- Entities endpoint mixes every tenant's values, so `memory_entities` counts the tenant's own memories instead, at most 1000 (the list ceiling, reported as `incomplete`)
- Single-ID endpoints are globally scoped — ownership verified via fetch-then-verify
- `memory_history` entries carry `author: null` for every event — mem0's history log is entirely upstream-managed and has no field to record it in. A memory's current `author` (from `search_memory`, `get_memory`, `list_memories`) is unaffected; only the event-by-event trail cannot be attributed

**`sqlite` and `in_memory` — no model behind them:**

- Retrieval is keyword matching, not vector similarity. Tokens match when they are equal or share a four-character prefix, so `linter` finds `linting` but a question phrased in different words finds nothing
- `add_memory` stores content verbatim. There is no fact extraction, so `infer` is accepted and ignored; `memory_status` reports `extracts_facts: false`
- No knowledge graph, so `memory_entities` is not registered at all

**`in_memory`, additionally:** loses all data on restart.

**Every backend:**

- No date/time-based filtering on search or list
- No rate limiting — configure it at the reverse proxy
- `delete_all_memories` deletes by scope structure, not content match

## Moving memories between backends

`export_memories` and `import_memories` move a tenant's memories from one backend to
another; `import_memories` takes at most 1,000 memories per call, and an export from
`mem0` stops at its list ceiling (above). Content, scope and metadata survive. These do
not, for any pair of backends:

| Lost | Why |
|---|---|
| Memory ids | The target assigns its own. Refer to memories by content and scope, not id. |
| `created_at`, `updated_at` | Imported memories are new on the target. |
| History | Exports carry current content only, not the change log. |
| `author` | Imports are stamped with the importing token's seat, or none on a server without tokens. |

Retrieval follows the target: after a move from `mem0` to `sqlite` or `in_memory`,
search is keyword matching (see Known limitations).

## See also

- [deployment.md](deployment.md) — Docker Compose, backends, tokens, reverse proxies
- [tests/mem0/README.md](../tests/mem0/README.md) — the conformance suite against a real mem0
