# Reference

Environment variables, the MCP tool surface, and what memcp does not do. Consulted
mid-task rather than read through. For getting a deployment running, see
[deployment.md](deployment.md).

## Requirements

- Docker, for `memcp up`
- Python 3.12+, to run the server directly
- For `MEMCP_BACKEND=mem0`: a running [mem0](https://github.com/mem0ai/mem0) instance — or let `memcp up --backend mem0` provision one

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MEMCP_BACKEND` | No | Backend for the server itself: `mem0`, `sqlite` or `in_memory`. Defaults to `mem0` when you run the server directly; `memcp up` provisions `sqlite` unless you pass `--backend` |
| `MEMCP_SQLITE_PATH` | No | Database file for the sqlite backend (default: `memcp.sqlite3`) |
| `MEM0_API_BASE` | mem0 | Base URL of your mem0 REST API |
| `MEM0_API_KEY` | mem0 | API key for the mem0 server |
| `MEMCP_AUTH_TOKENS` | No | Token-to-tenant mapping: `tok1:alice,tok2:bob`. Parsed exactly as before this project added attribution — a `user_id` containing a colon still round-trips unchanged. Unset means unauthenticated, which is refused on any non-loopback bind |
| `MEMCP_AUTH_SEATS` | No | Optional token-to-seat mapping, layered onto `MEMCP_AUTH_TOKENS`: `tok1:agent-one,tok2:agent-two`, seat matching `[A-Za-z0-9_.-]+`. What memcp stamps as `author` on every write that token makes. A token named here with no `MEMCP_AUTH_TOKENS` entry is rejected at startup. Two tokens can share a tenant with different seats — that is how one shared token becomes individually attributable — and a token absent from this mapping keeps its seat equal to its tenant, as it always has |
| `MEMCP_HOST` | No | Bind address (default: `0.0.0.0`) |
| `MEMCP_PORT` | No | Bind port (default: `8080`) |
| `MEMCP_ALLOWED_HOSTS` | No | Host header allow-list for DNS-rebinding protection, comma-separated, `:*` for any port. Unset leaves the MCP SDK's rule, which is off unless `MEMCP_HOST` is loopback |
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

### Universal — registered on every backend

| Tool | Description |
|---|---|
| `add_memory` | Store a fact/preference/decision. On `mem0`, extracts facts by default (may store nothing) and `infer=false` stores verbatim. On `sqlite` and `in_memory` there is no model, so content is always stored verbatim and `infer` has no effect — the tool's own description says which, per deployment. Bulk: use `import_memories` |
| `search_memory` | Ranked search — semantic on `mem0`, keyword on `sqlite` and `in_memory`. `threshold` filters by minimum score (0-1). For browsing: `list_memories` |
| `delete_memory` | Delete one memory by ID. Confirm with user first |
| `delete_all_memories` | Bulk-delete by scope (e.g. agent_id, run_id), not content. Requires at least one scope key. Confirm first |
| `memory_status` | Returns server version, backend type, capabilities, valid scope keys, whether the backend extracts facts, and whether retrieval is semantic or keyword. No memory content |

### Optional — registered only by a backend that implements them

| Tool | Description |
|---|---|
| `get_memory` | Fetch one memory by ID. Returns full content, scope, and metadata |
| `update_memory` | Full-replace a memory's content (not a patch). Scope immutable — to change scope, add new + delete old |
| `list_memories` | Browse memories, optionally filtered by scope. Unranked, paginated. For semantic queries: `search_memory` |
| `export_memories` | Export memories as JSON (max 10k, truncates with flag). For backup/migration. Output compatible with `import_memories` (requires `list_memories`) |
| `import_memories` | Batch-import from JSON. Dedup via exact content match, scope-aware. `on_conflict`: skip, overwrite, duplicate (requires `list_memories`; overwrite requires `update_memory`) |
| `memory_history` | Change log for a memory: timestamps and previous/current content per create/update event |
| `memory_entities` | Knowledge graph: entities and relationships. Registered only by a backend that has one — `mem0` does, `sqlite` and `in_memory` do not, so it is absent on a keyless install. Not a search tool — use `search_memory` for topics |

## Known limitations

**mem0 backend — upstream constraints:**

- Nested boolean filters (AND/OR/NOT) return 502 — use flat scope keys
- List endpoint does not paginate server-side — full dataset loaded per request
- List returns at most 1000 memories per call (mem0's own ceiling), so an export of a tenant above that is incomplete and does not say so
- List endpoint does not filter by metadata
- Entities endpoint does not filter by user — post-filtered client-side
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

What survives a move between two backends, and what does not, is declared per pair
in [portability.md](portability.md) and asserted against.

## See also

- [deployment.md](deployment.md) — `memcp up`, backends, credentials, reverse proxies
- [conformance.md](conformance.md) — holding a backend to the suite, in this repository or out of it
- [development.md](development.md) — the local check loop
