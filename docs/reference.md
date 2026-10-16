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
| `MEMCP_KEYS_FILE` | No | Path to the [keys file](#keys-file). Unset means one key, `local`, with `writer` on the space `default_user`, which is refused on any non-loopback bind. `docker-compose.yml` sets it to `/etc/memcp/keys.toml` |
| `MEMCP_AUTH_TOKENS`, `MEMCP_AUTH_SEATS` | — | Replaced by the keys file. memcp refuses to start while either is set |
| `MEMCP_HOST` | No | Bind address (default: `0.0.0.0`) |
| `MEMCP_PORT` | No | Bind port (default: `8080`) |
| `MEMCP_ALLOWED_HOSTS` | No | Host header allow-list for DNS-rebinding protection, comma-separated, `:*` for any port. `docker-compose.yml` defaults it to `127.0.0.1:*,localhost:*`; a value replaces that. Without Docker, unset leaves the MCP SDK's rule, which is off unless `MEMCP_HOST` is loopback |
| `MEMCP_LOG_LEVEL` | No | Log level (default: `INFO`). The [audit log](#audit-log) stays at `INFO` |
| `MEMCP_LOG_FORMAT` | No | Log format: `json` or `plain` (default: `json`) |

`.env.example` carries the same set in the shape a deployment reads it.

## Keys file

A TOML file of keys. Each request is made by the key its bearer token belongs to.

```toml
[[keys]]
name = "laptop"
secrets = ["sha256:<64 hex digits>"]
grants = [{ space = "notes", relation = "writer" }]
```

| Field | Rule |
|---|---|
| `name` | Required and unique; `[A-Za-z0-9_.-]`, 1 to 64 characters. Recorded as `author` on the key's writes |
| `secrets` | One or more `sha256:` plus the 64 lowercase hex digits of a token's SHA-256. Each secret belongs to one key |
| `grants` | Exactly one `{ space, relation }` |
| `space` | The space the key reaches. Not `*` or empty, and on mem0 without whitespace at either end |
| `relation` | `reader` or `writer` |

memcp refuses to start on a missing file, invalid TOML, an unknown field or any rule above.
Any other space with whitespace or control characters, more than 128 characters, or a
`spc_` or `__` prefix is logged as a warning and served. memcp reads the file at startup only.

| Tool | Relation |
|---|---|
| `search_memory`, `list_memories`, `get_memory`, `memory_history`, `memory_entities`, `export_memories` | `reader` or `writer` |
| `add_memory`, `update_memory`, `delete_memory`, `delete_all_memories`, `import_memories` | `writer` |
| `memory_status` | any key |

## MCP tools

Names, argument schemas and behaviour annotations are frozen in
[tool-surface.json](tool-surface.json); a test fails on any drift.

Every memory a read tool returns carries `author`: the name of the key whose write last
changed it, set by the server — a caller cannot set it. Without a keys file it is `local`.
It is `null` for a memory written by 0.1.2 or earlier, which didn't record authors. It
records which key wrote the memory, not where the facts came from —
see the server's own MCP instructions for how a client is told to treat it.

sqlite and in_memory keep `author` outside metadata, where no caller can write it. mem0
keeps it in metadata under the reserved key `_memcp_system`. memcp refuses `_memcp_` keys
in callers' metadata, but 0.1.2 and earlier stored them as given, and memcp can't
enumerate a mem0 install's spaces to clean them. **Sweep a mem0 store for `_memcp_system`
before this version serves it.** Otherwise a key planted earlier reads back as an `author`
no server set, and a malformed one fails every read that returns it with `internal_error`.

### Scope

A scope labels memories inside a space; `memory_status` lists the keys the backend
supports (`agent_id` and `run_id` on every bundled backend). Every tool that takes a scope
applies the same rules:

- `user_id` is refused; the server sets it from your key.
- Empty, `*` and `null` values are refused.
- `agent_id` is normalized: lowercased, trimmed, every run of other characters turned into
  one hyphen, and hyphens at either end dropped.
- An `agent_id` with no letters or digits is refused.
- Any other value with whitespace at either end is refused, and so is a `run_id` with
  whitespace inside it.
- A number that isn't finite (NaN or infinity) is refused.

Scope values are stored as text: a `run_id` sent as `7` reads back as `"7"`, and `true` as
`"true"`. A filter matches on that text, so `7` and `"7"` find the same memories and `7.0`
does not.

Search and list leave out a memory the backend returns outside the requested scope, and log
a warning (`memcp.policy`).

### Metadata

`add_memory`, `update_memory` and `import_memories` take `metadata`: a flat map of keys to
strings, numbers, booleans or lists of strings. The same rules apply on every backend:

- A reserved key is refused, and so is any key starting with `_memcp_`. The reserved keys
  are `actor_id`, `agent_id`, `attributed_to`, `created_at`, `data`, `expiration_date`,
  `hash`, `id`, `memory_type`, `role`, `run_id`, `text_lemmatized`, `updated_at` and
  `user_id`.
- A nested object, a list holding anything but strings, and a number that isn't finite are
  refused.
- A key is at most 64 characters, and a memory's metadata is at most 16 KiB as JSON. An
  update that removes keys, or otherwise doesn't grow the metadata, is accepted even when
  it is still over the cap, and it can remove a key over 64 characters, so metadata
  stored before these rules can be cleaned up.

A write that breaks a rule gets `validation_error`, and nothing is stored. The message
names the key for every rule but the size cap. `import_memories` lists the entry in its
`errors` instead.

On `update_memory`, `metadata` is a patch: a key with a value is set, a key sent as `null`
is removed, and a key left out is kept. Removing a key that isn't there does nothing, and a
list replaces the stored list whole. `null` is accepted only there, and a removed key never
reads back. `import_memories` with `on_conflict=overwrite` replaces the metadata instead:
the memory ends with the entry's keys, plus any reserved key already stored. An entry
without `metadata` leaves the stored metadata as it is, and `metadata: {}` clears it.

### Universal — registered on every backend

| Tool | Description |
|---|---|
| `add_memory` | Store a fact/preference/decision. On `mem0`, extracts facts by default (may store nothing) and `infer=false` stores verbatim. On `sqlite` and `in_memory` there is no model, so content is always stored verbatim and `infer` has no effect — the tool's own description says which, per deployment. Each result's `event` is `add`, `update` or `delete`. Bulk: use `import_memories` |
| `search_memory` | Ranked search — semantic on `mem0`, keyword on `sqlite` and `in_memory`. `threshold` filters by minimum score (0-1). For browsing: `list_memories` |
| `get_memory` | Fetch one memory by ID. Returns full content, scope, and metadata |
| `delete_memory` | Delete one memory by ID. Confirm with user first |
| `delete_all_memories` | Bulk-delete by scope (e.g. agent_id, run_id), not content. Requires at least one scope key, each with a concrete value (not empty, `*` or null). Confirm first |
| `memory_status` | Returns server version, backend type, capabilities, valid scope keys, whether the backend extracts facts, and whether retrieval is semantic or keyword. No memory content |

### Optional — registered only when the backend declares the operation behind them

| Tool | Description |
|---|---|
| `update_memory` | Change a memory's content, its metadata (a patch: see Metadata), or both. Scope immutable — to change scope, add new + delete old |
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
| `invalid_scope` | The scope names `user_id`, or a key the backend doesn't support | `false` |
| `nested_filter` | The scope uses `AND`, `OR` or `NOT` | `false` |
| `scope_required` | `delete_all_memories` got an empty scope | `false` |
| `forbidden` | The key's relation doesn't allow the tool: a `reader` called a tool that needs `writer` | `false` |
| `not_found` | The memory doesn't exist or belongs to another space | `false` |
| `not_supported` | `import_memories` with `on_conflict=overwrite` on a backend without `update_memory` | `false` |
| `internal_error` | A memory the backend returned, or one memcp is about to change or has just stored, is in another space or can't be read back. Nothing from it is returned, and the server logs `isolation_violation`. On add, update and import, the write may already be stored | `false` |
| `timeout` | The backend didn't answer in time | `true` |
| `backend_error` | The backend returned an error | `true` for a 5xx, otherwise `false` |

A `timeout` or `backend_error` message gives at most the status, never the store's
response. The server logs that response as a warning, cut to 2,000 characters; it can quote
what the request sent, memory content included.

A rejected entry doesn't fail `import_memories`: it imports the entries it can and lists
each rejected one in its result's `errors`, with the entry's `index` and the reason in
`error`. For an entry the backend fails, `error` holds the same fixed message; the server
logs only the first failure's response, and one line counting the rest.

With a keys file, a request without a bearer token, or with one no key lists, gets HTTP
401 with the same object and the code `unauthorized`.

## Audit log

Every memory operation writes one record to the logger `memcp.audit`, whether it succeeds
or is refused, at `INFO` whatever `MEMCP_LOG_LEVEL` says. A JSON log line carries the
record as an `audit` object; a plain line carries the same fields as `field=value` text. A
record holds no content, query or metadata. Three refusals write none, since no memory
operation starts: a request refused with 401, a tool call whose arguments don't match its
schema, and a `migrate()` call with a blank operator.

| Field | Value |
|---|---|
| `operation` | `add`, `search`, `get`, `update`, `delete`, `delete_all`, `list_memories`, `history`, `entities`, `export_memories`, `import_memories` or `migrate` |
| `key` | The key's name; for `migrate`, the operator its caller names |
| `space` | The key's space; for `migrate`, the target space |
| `relation` | `reader` or `writer`; `null` for `migrate` |
| `outcome` | `ok`, or the [error code](#errors) the call returned; `validation_error` for a refused migration, `cancelled` for a call the client abandoned, `error` for an unexpected failure |
| `ids` | The ids `add` stored, or the id `get`, `update`, `delete` or `history` named, if it is a valid id |
| `count` | The results of `search` and `list_memories`, the memories exported, imported or migrated, the entity rows, or the memories `delete_all` deleted (`null` when the backend doesn't say) |
| `scope` | The validated scope, for a call that takes one |
| `source_space` | For `migrate`, the source space; otherwise `null` |

## Known limitations

**mem0 backend — upstream constraints:**

- Nested boolean filters (AND/OR/NOT) return 502 — use flat scope keys
- List endpoint does not paginate server-side — up to 1000 memories are loaded per request, and memcp pages them
- List returns at most 1000 memories (mem0's own ceiling) and can't page past it, so a space at or above 1000 memories is reported as incomplete (see each tool's flag above)
- List endpoint does not filter by metadata
- Entities endpoint mixes every space's values, so `memory_entities` counts the space's own memories by agent and run instead, at most 1000 (the list ceiling, reported as `incomplete`)
- Single-ID endpoints are globally scoped — ownership verified via fetch-then-verify
- `memory_history` entries carry `author: null` for every event — mem0's history log is entirely upstream-managed and has no field to record it in. A memory's current `author` (from `search_memory`, `get_memory`, `list_memories`) is unaffected; only the event-by-event trail cannot be attributed

**`sqlite` and `in_memory` — no model behind them:**

- Retrieval is keyword matching, not vector similarity. Tokens match when they are equal or share a four-character prefix, so `linter` finds `linting` but a question phrased in different words finds nothing
- In Chinese, Japanese, Korean, Thai, Lao, Khmer and Myanmar, each run of characters in a query, with any number or word written against it as in `3月`, must appear in the memory as written: the words of these scripts aren't separated by spaces, or carry their particles
- `add_memory` stores content verbatim. There is no fact extraction, so `infer` is accepted and ignored; `memory_status` reports `extracts_facts: false`
- No knowledge graph, so `memory_entities` is not registered at all

**`in_memory`, additionally:** loses all data on restart.

**Every backend:**

- No date/time-based filtering on search or list
- No rate limiting — configure it at the reverse proxy
- `delete_all_memories` deletes by scope structure, not content match

## Moving memories between backends

`export_memories` and `import_memories` move a space's memories from one backend to
another; `import_memories` takes at most 1,000 memories per call, and an export from
`mem0` stops at its list ceiling (above). Content, scope and metadata survive, except a
memory whose metadata breaks the metadata rules, which import lists in `errors`. These do
not, for any pair of backends:

| Lost | Why |
|---|---|
| Memory ids | The target assigns its own. Refer to memories by content and scope, not id. |
| `created_at`, `updated_at` | Imported memories are new on the target. |
| History | Exports carry current content only, not the change log. |
| `author` | Imports are stamped with the importing key's name. |

Retrieval follows the target: after a move from `mem0` to `sqlite` or `in_memory`,
search is keyword matching (see Known limitations).

## See also

- [deployment.md](deployment.md) — Docker Compose, backends, keys, reverse proxies
- [tests/mem0/README.md](../tests/mem0/README.md) — the conformance suite against a real mem0
