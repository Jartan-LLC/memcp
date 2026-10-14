# Architecture

How memcp is divided, what each part owns, and the rules a change is checked against.
For running memcp, see [deployment.md](deployment.md); for settings and tools,
[reference.md](reference.md).

## Terms

| Term | Meaning |
|---|---|
| Space | Where memories live, and the unit of isolation. Every memory belongs to exactly one space, for life. |
| Partition | The string a backend stores to mark a memory's space. Opaque to the backend, and never shown to a client. |
| Key | A configured identity: a name, one or more secrets (bearer tokens), and its grants. Every request is made by one key. |
| Grant | A key may use a space with a relation. |
| Relation | `reader` (read memories) or `writer` (read, add, change and delete any memory in the space). |
| Scope | `agent_id` and `run_id`: labels that organize memories inside a space. Not a security boundary. |
| System fields | `system["space"]` (the partition), `system["author"]` (name of the key that last wrote the memory) and `system["created_by"]` (name of the key that created it). Set only by policy. |
| Policy decision | A decision about what memcp accepts from a client or returns to it: the space a call reaches, allowed scope values, the author, result counts. |

## Packages

Calls go down the table, never up.

| Package | Owns | Never |
|---|---|---|
| `memcp/http/` | HTTP, Host checks, `/health`, resolving a bearer token to its key; `app.py` builds the backend and the policy object at startup | Calls a backend method |
| `memcp/tools/` | The MCP tools: names, argument schemas, result shapes, error responses. Calls the policy object it is given | Makes a policy decision, or holds a backend |
| `memcp/policy/` | Every policy decision; import and export; the only caller of a backend method | Knows a store's API |
| `memcp/backends/` | One adapter per memory store (mem0, sqlite, in_memory): storing, searching, fetching, history | Makes a policy decision |

Shared by all of them and importing nothing else from memcp: `types.py` (`Memory`,
`HistoryEntry`, result types, `MemoryAPIError`), `identity.py` (key, grant, the current
request's key) and `config.py`.

```text
memcp/
  __main__.py, config.py, types.py, identity.py
  http/       app.py, auth.py (keys file, token resolution), hosts.py, logging.py
  tools/      __init__.py (register_tools), memories.py, bulk.py, transfer.py, insight.py, surface.py
  policy/     __init__.py (one method per operation), access.py, scope.py, metadata.py,
              results.py, transfer.py, audit.py
  backends/   base.py (the contract), capabilities.py, keyword.py, in_memory.py, sqlite.py,
              mem0/ (client.py, adapter.py)
```

Tests mirror the packages (`tests/http/`, `tests/tools/`, `tests/policy/`,
`tests/backends/`); `tests/conformance/` holds every backend to its contract.

## Rules for changes

1. Each policy decision is made once, in `memcp/policy/`. A backend also enforces the
   store invariants listed under "What a backend guarantees", even though policy checks
   the same input first; that second layer is deliberate. A backend never relaxes or
   reinterprets a policy decision.
2. A module imports only from packages below it in the table, or from `types.py`,
   `identity.py` and `config.py`. Modules inside `backends/` may import each other. A test
   fails on any other import.
3. Only `http/app.py` constructs a backend, and only `policy/` calls a backend method
   (health and shutdown included). A test checks the calls.
4. Adding a backend means writing one class that implements `backends/base.py` and declares
   its optional capabilities. Needing an edit to `tools/` or `policy/` beyond registering it
   means the contract is missing something.
5. Policy validates every argument before calling a backend, and checks every result it can
   on the way back (the checks are listed per operation below).
6. Each policy decision has a test that calls `policy/` directly with input that breaks it.
   Each backend guarantee has a test in `tests/conformance`, run against every backend.

## Keys and access

Keys are configured in a TOML file whose path `MEMCP_KEYS_FILE` gives:

```toml
[[keys]]
name = "laptop-claude"
secrets = ["sha256:9f86d0…"]          # two entries during a rotation
grants = [{ space = "jon", relation = "writer" }]

[[keys]]
name = "ci-reader"
secrets = ["sha256:2c26b4…"]
grants = [{ space = "jon", relation = "reader" }]
```

- A secret is stored as the SHA-256 of the token, so the file holds nothing that grants
  access and can be version-controlled.
- Names are unique and required, so every write has an author. All of a key's secrets have
  the same access.
- Each key has exactly one grant. Several grants per key, `manager`, and a registry of keys
  managed through an API or UI are planned in
  [#112](https://github.com/Jartan-LLC/memcp/issues/112). The registry resolves tokens to
  the same keys and grants, so policy doesn't change; a deployment uses the keys file or
  the registry, never both.
- **Without a keys file,** memcp runs as a single local key named `local` with `writer` on
  the space `default_user`, and refuses to bind any interface other than loopback.
- **With a keys file,** a request whose token resolves to no key is refused with 401;
  nothing falls back to a default key.

Startup refuses: a missing or malformed keys file, a duplicate name, a secret listed under
two keys, a key with no grant, and a space named `*` or empty. A space whose name another
store couldn't match exactly (whitespace, control characters, more than 128 characters, a
`spc_` or `__` prefix) is logged as a warning and served.

### Relation needed per tool

| Tool | Relation |
|---|---|
| `search_memory`, `list_memories`, `get_memory`, `memory_history`, `memory_entities`, `export_memories` | `reader` or `writer` |
| `add_memory`, `update_memory`, `delete_memory`, `delete_all_memories`, `import_memories` | `writer` |
| `memory_status` | none |

A key without the relation gets `forbidden`. A memory in another space and a missing
memory both get `not_found`.

## Spaces

memcp enforces tenancy itself, whatever the store supports. On mem0, the adapter also maps
the partition to mem0's `user_id`, so mem0's own filter applies to search, list and bulk
delete; mem0's by-id calls don't check ownership, so there memcp's checks are the only
isolation.

### How policy enforces it

The space comes from the key's grant, never from arguments, metadata or an import payload.
Every backend call names exactly one partition, and no partition or system field other
than the author is ever returned to a client.

| Operation | Before calling the backend | After |
|---|---|---|
| add, import | Set the partition and all system fields; refuse caller-supplied `system` or reserved metadata | Check each returned item's action (added, updated, deleted) and audit each |
| search, list, export | Scope validated; partition set | Every memory's stored partition and `system["space"]` must equal the partition asked for; otherwise the request fails |
| get, history | — | Same check as search |
| update, delete | Fetch the memory and check its space | Check the updated memory |
| delete_all | Refuse an empty scope or an empty, null or wildcard value | Nothing can be checked after a bulk delete; the backend's store invariants are the protection |
| entities | Partition set | The partition is never returned as an entity |
| status, health | — | — |

A memory written before stamps existed is checked on its stored partition alone. On a
failed check, an `isolation_violation` error is logged without content and the client
gets `internal_error`.

Export and import carry content, scope and metadata only. Import writes into the
importing key's space and stamps it as that key's write. `migrate()` is an operator
function for moving memories between backends: it takes source and target partitions
directly, isn't reachable by a client, and is audited.

All keys with `writer` on one space trust each other. On a backend that extracts facts
(`infer=True`), `system["author"]` records the key that called add, not whose words the
facts came from.

Every policy decision is logged to a structured audit logger: operation, key name (never a
token), space, memory ids or scope and count, relation, outcome. Never content.

## What a backend guarantees

`backends/base.py` is the contract. A backend:

- takes `partition: str` on every data method and stores it byte for byte;
- filters search, list, entities and bulk delete by exact equality on the partition, ANDed
  with the scope, and raises on a scope key it doesn't support rather than ignoring it;
- returns, on every memory, the partition it stored and the `system` fields it stored, as
  stored, never copied from the request;
- stores `system` as policy passes it, on add and on update;
- implements `get`; `get` returns `None`, and `update`, `delete` and `history` raise
  `MemoryAPIError` 404, when the id is missing or belongs to another partition;
- never changes a memory's partition;
- returns a `next_cursor` from `list_memories` whenever more memories exist;
- reports each add item's action: added, updated or deleted;
- refuses a partition its store can't match by exact equality, on every call
  (`validate_partition`);
- refuses `delete_all` with an empty scope or an empty, null or wildcard value;
- declares its optional capabilities, and raises `NotImplementedError` for the rest;
- maps store errors to `MemoryAPIError`, keeping the store's response out of the message.

Where each backend keeps the system fields: a `system` column on sqlite, a field on
in_memory entries, a reserved metadata key on mem0.

`tests/conformance` checks each guarantee against every backend, including isolation
between two spaces for every operation, partitions that differ only by prefix, case or
Unicode normalization, and paging past a backend's page size.

## Policy beyond spaces

- **Scope:** one function validates every scope before any backend call: allowed keys,
  value types and lengths, key count, `agent_id` normalization. It refuses `user_id`,
  nested filters, and empty, null or wildcard values.
- **Metadata:** keys under the reserved prefix `_memcp_` are refused on input and removed
  from output.
- **Limits:** page sizes, result counts and import sizes outside their ranges are refused.
- **Errors:** a backend error reaches the client as a fixed code and message; the store's
  response goes to the server log only.
- **Capabilities:** a tool exists only when the backend declares the capability behind it.

## Backend-specific tools

Not built yet; [#107](https://github.com/Jartan-LLC/memcp/issues/107) builds it.

A backend can offer MCP tools beyond the standard set when its store can do something they
can't express, such as a graph backend answering "what's connected to project X?". The
backend declares each tool's name, argument schema and handler, and the handler returns
memcp types that carry a partition, so policy's checks apply to its results as to a
standard tool's. `tools/` registers it under a name prefixed with the backend (`mem0_…`)
and refuses a name that collides with another tool. These tools are versioned with their
backend and kept out of the frozen surface in `docs/tool-surface.json`.
