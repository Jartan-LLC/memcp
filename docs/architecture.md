# Architecture

How memcp is divided, what each part owns, and the rules a change is checked against.
For running memcp, see [deployment.md](deployment.md); for settings and tools,
[reference.md](reference.md).

## Terms

| Term | Meaning |
|---|---|
| Store | The database or service that holds memories: a mem0 server, a SQLite file, process memory. |
| Backend | memcp's class for one kind of store, implementing `backends/base.py`. |
| Space | Where memories live, and the unit of isolation. Every memory belongs to exactly one space, for life. |
| Partition | The string a backend stores to mark a memory's space. Opaque to the backend, and never shown to a client. A space's partition is its name in the keys file. |
| Key | A configured identity: a name, one or more secrets (bearer tokens), and one grant. Every request is made by one key. |
| Grant | A key's permission to use one space with one relation. |
| Relation | `reader` (read memories) or `writer` (read, add, change and delete any memory in the space). |
| Scope | `agent_id` and `run_id`: labels that organize memories inside a space. Not a security boundary. |
| System fields | `system["space"]` (the partition), `system["author"]` (name of the key that last wrote the memory) and `system["created_by"]` (name of the key that created it). Set only by policy. |
| Policy decision | A decision about what memcp accepts from a client or returns to it: the space a call reaches, what a key's grant allows, scope, authorship, metadata and limits. |
| Policy object | The one instance of `policy.Policy` that `http/app.py` builds around the backend; `tools/` calls only it. |
| Capabilities | What a backend declares about itself: its optional operations (`update`, `list`, `history`, `entities`), its retrieval kind, whether it extracts facts, and its list ceiling (the most memories its `list` can reach), if it has one. |

## Packages

Calls go down the table, never up.

| Package | Owns | Never |
|---|---|---|
| `memcp/http/` | HTTP, Host checks, `/health`, resolving a bearer token to its key; `app.py` builds the backend and the policy object at startup | Calls a backend method |
| `memcp/tools/` | The MCP tools: names, argument schemas, result shapes, error responses. Calls the policy object it is given, and reads the backend's capabilities from it | Makes a policy decision, or holds a backend |
| `memcp/policy/` | Every policy decision, including what each key may do; import, export and migration; the only caller of a backend method; removes the partition and every system field but the author from what it returns | Knows a store's API |
| `memcp/backends/` | One backend per kind of store (mem0, sqlite, in_memory): storing, searching, fetching, history | Makes a policy decision |

Shared by all of them and importing nothing else from memcp: `types.py` (`Memory`,
`HistoryEntry`, result types, `MemoryAPIError`), `identity.py` (key, grant, the current
request's key, which has no default: reading it when no key is set raises) and `config.py`.

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

1. Make each policy decision once, in `memcp/policy/`. A backend also enforces the store
   invariants listed under "What a backend guarantees"; don't remove them as duplicates of
   policy's checks. A backend never relaxes or reinterprets a policy decision.
2. A module imports only from packages below it in the table, or from `types.py`,
   `identity.py` and `config.py`. Modules inside `backends/` may import each other. A test
   fails on any other import.
3. In the server, only `http/app.py` constructs a backend, and only `policy/` calls a
   backend method, health and shutdown included. The tool-surface generator constructs its
   own backend and wraps it in a policy object, as `http/app.py` does. Only tests call a
   backend directly. A test fails on any other construction or call.
4. To add a backend, write one class that implements `backends/base.py` and declares its
   optional capabilities. Outside `backends/`, change only the backend setting in
   `config.py` and its construction in `http/app.py`. If `tools/` or `policy/` would need
   an edit, extend the contract instead.
5. Policy validates every argument before calling a backend, and checks every result it can
   on the way back (the checks are listed per operation below).
6. Each policy decision has a test that calls `policy/` directly with input that breaks it.
   Each backend guarantee has a test in `tests/conformance`, run against every backend.

## Keys and access

Keys are configured in a TOML file whose path `MEMCP_KEYS_FILE` gives:

```toml
[[keys]]
name = "laptop-claude"
secrets = ["sha256:<64 hex digits>"]  # two entries during a rotation
grants = [{ space = "team-notes", relation = "writer" }]

[[keys]]
name = "ci-reader"
secrets = ["sha256:<64 hex digits>"]
grants = [{ space = "team-notes", relation = "reader" }]
```

- A token is generated with `openssl rand -hex 32` or an equivalent random source. The file
  holds its SHA-256, so it holds nothing that grants access and can be version-controlled:

  ```bash
  printf 'sha256:%s\n' "$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)"
  ```

- Names are unique and required, so every write has an author. A key's name never changes
  and is never given to another key, since stored memories record it. All of a key's
  secrets have the same access.
- Each key has exactly one grant.
- Planned in [#112](https://github.com/Jartan-LLC/memcp/issues/112): a registry of spaces,
  keys and grants managed through an API or UI, several grants per key, choosing a space
  per call, reads across spaces, a `manager` relation for administering a space, deleting
  a whole space with each
  backend declaring how completely it erases, users and organizations, and shared spaces.
  The registry resolves tokens to the same keys and grants, so policy doesn't change; a
  deployment uses the keys file or the registry, never both.
- **When `MEMCP_KEYS_FILE` is unset,** memcp runs as a single local key named `local` with
  `writer` on the space `default_user`, and refuses to bind any interface other than
  loopback.
- **When it is set,** a request with no token, or one that resolves to no key, is refused
  with 401; nothing falls back to a default key.

Startup refuses:

- `MEMCP_AUTH_TOKENS` or `MEMCP_AUTH_SEATS` set, since the keys file replaces them;
- `MEMCP_KEYS_FILE` set to a file that is missing or isn't valid TOML;
- an unknown field;
- a name outside `[A-Za-z0-9_.-]{1,64}`, or a duplicate name;
- a secret not of the form `sha256:` plus 64 lowercase hex digits, or one listed under two
  keys;
- a key with no grant or more than one;
- a relation other than `reader` or `writer`;
- a space named `*` or empty, or one the configured backend's `validate_partition` refuses
  (policy checks each granted space when `http/app.py` builds the policy object).

A space name that isn't portable to every backend (whitespace, control characters, more
than 128 characters) or that starts with `spc_` or `__` is logged as a warning and served.

### Relation needed per tool

| Tool | Relation |
|---|---|
| `search_memory`, `list_memories`, `get_memory`, `memory_history`, `memory_entities`, `export_memories` | `reader` or `writer` |
| `add_memory`, `update_memory`, `delete_memory`, `delete_all_memories`, `import_memories` | `writer` |
| `memory_status` | any key |

A key without the relation gets `forbidden`. A memory in another space and a missing
memory both get `not_found`.

## Spaces

memcp keeps spaces apart itself, whatever the store supports. On mem0, the backend also maps
the partition to mem0's `user_id`, so mem0's own filter applies to search, list and bulk
delete; mem0's by-id calls don't check ownership, so there memcp's checks are the only
isolation.

### How policy keeps spaces apart

The space comes from the key's grants: no argument, metadata or import payload can reach a
space the key isn't granted. With one grant per key, that is always the granted space.
Every backend call names exactly one partition.

The **space check** on a memory: its stored partition, and its `system["space"]` when it
has one, equal the partition policy asked for. A failed space check fails the request; an
`isolation_violation` event is logged without content, and the client gets the error code
`internal_error`.

| Operation | Before calling the backend | After |
|---|---|---|
| add | Set the partition; set `system` to `{space, author, created_by}`, author and creator both the calling key; refuse caller-supplied `system` or reserved metadata | For each item: an `added` or `updated` item is fetched and space-checked (an `updated` item keeps its stored `created_by`, which the backend guarantees); a `deleted` item is audited by id |
| search, list, export | Scope validated; partition set | Space check on every memory. A memory whose scope doesn't contain the requested scope is dropped, with a warning |
| get | — | Space check |
| update | Fetch the memory and space-check it; send `system` with its stored `created_by` (absent if it has none), `author` set to the calling key, and `space` | Space check on the updated memory |
| delete, history | Fetch the memory and space-check it | — |
| delete_all | Refuse an empty scope or an empty, null or wildcard value | Nothing can be checked after a bulk delete; the backend's store invariants are the protection |
| entities | Partition set | Entity rows carry no partition, so the backend's filter is the protection; the partition is never returned as an entity |
| import | As add for a new memory; as update for an overwrite; dedup reads the space's memories through list | As add and update |
| status, health | — | — |

**Import and export:** export returns each memory in the space with its id, timestamps,
author, content, scope and metadata; import reads only content, scope and metadata, writes into the
importing key's space, writes content as given (`infer=False`), and stamps each memory as
that key's write. Export, import's dedup read and `migrate()` follow `next_cursor` until
the list ends or memcp's export cap (`MAX_EXPORT`) is reached. An export or migration that
stops at the cap, or reads a list the backend marked incomplete, is marked truncated.

**Migration:** `migrate()` is an operator function for moving memories between backends.
It isn't reachable by a client. It takes source and target partitions directly, refused
as a granted space would be (`*`, empty, or refused by that backend's
`validate_partition`); space-checks every memory it reads from the source; and writes each
to the target as given (`infer=False`), with `system` copied from the source and `space`
set to the target partition. It is audited as the operation `migrate`; its caller passes an
operator name, which the audit records as the key name.

**Trust within a space:** all keys with `writer` on one space trust each other. On a backend
that extracts facts (`infer=True`), `system["author"]` records the key that called add, not
whose words the facts came from.

## What a backend guarantees

`backends/base.py` is the contract. A backend:

- takes `partition: str` on every data method and stores it byte for byte;
- filters search, list, entities and bulk delete by exact equality on the partition, ANDed
  with the scope, and raises on a scope key it doesn't support rather than ignoring it;
- returns, on every memory, the partition it stored and the `system` fields it stored, as
  stored, never copied from the request;
- stores `system` as policy passes it, on add and on update, except that an add item it
  reports as `updated` keeps the memory's stored `created_by` and `space`;
- refuses a metadata key under `_memcp_` or in the reserved set with `MemoryAPIError` 400,
  on add and on update, whether the key is set or removed;
- implements the required methods: `add`, `search`, `get`, `delete`, `delete_all`,
  `scope_keys`, `validate_partition`, `health` and `close`;
- for an id that is missing or belongs to another partition, returns `None` from `get` and
  raises `MemoryAPIError` 404 from `update`, `delete` and `history`;
- never changes a memory's partition;
- on update, applies the metadata patch policy passes (keys to set, keys to remove), leaving
  every other stored key as it is, and changes the content only when content is given;
- returns scope values as strings, compared byte for byte;
- returns search results sorted by `score`, a number from 0 to 1 where higher is better
  (policy applies the threshold to that score; a backend may use it as a hint);
- returns a `next_cursor` from `list` whenever more memories exist within its declared
  list ceiling, and marks the result incomplete when memories exist beyond it;
- reports each add item's action: `added`, `updated` or `deleted`;
- refuses a partition its store can't match by exact equality, on every call
  (`validate_partition`);
- refuses `delete_all` with an empty scope or an empty, null or wildcard value;
- declares its optional capabilities by operation name (`update`, `list`, `history`,
  `entities`), its retrieval kind (keyword or semantic), whether it extracts facts, and its
  list ceiling if it has one, and raises `NotImplementedError` for an operation it doesn't
  declare;
- maps store errors to `MemoryAPIError`, keeping the store's response out of the message.

Where each backend keeps the system fields: a `system` column on sqlite, a field on
in_memory entries, the metadata key `_memcp_system` on mem0.

On mem0, a removed key is written as `null` (only when the key is stored), since mem0
can't delete a metadata key. The mem0 backend drops null-valued keys from everything it
returns.

The reserved metadata set and the wildcard and empty-value check are defined once, in
`backends/base.py`; policy and every backend import them.

`tests/conformance` checks each guarantee against every backend, including:

- isolation between two spaces for every operation, and partitions that differ only by
  prefix, case or Unicode normalization;
- a memory written to one partition and fetched by id from another returning `None`, which
  catches a backend that copies the requested partition into its results;
- paging past the page size, and the incomplete mark past the list ceiling;
- on update, a key outside the patch survives, a removed key reads back absent from get,
  list, search and export, removing a missing key does nothing, and a content-only or
  metadata-only update leaves the other unchanged;
- a `_memcp_` or reserved key refused on add, and on update both to set and to remove;
- on a backend that extracts facts, a second key's add that updates an existing memory
  leaving its `created_by` unchanged;
- scope values returned as strings, scores in range and sorted;
- a store error's response absent from the `MemoryAPIError` message.

## Policy beyond spaces

- **Scope:** one function validates every scope before any backend call: allowed keys,
  value types and lengths, key count, `agent_id` normalization. It refuses `user_id`,
  nested filters, and empty, null or wildcard values. `agent_id` is normalized first; any
  other value with whitespace at either end, or a `run_id` with whitespace inside it, is
  refused (mem0 trims the ends and refuses inner whitespace).
- **Metadata:** a flat map of keys to strings, numbers, booleans or lists of strings. On
  update, `metadata` is a patch: a key with a value is set, a key set to `null` is removed,
  and keys left out are kept; a client never sees `null`. Policy refuses, with a
  `validation_error` naming the key, a `null` value outside an update's patch, a nested
  object, a mixed list, a key under the prefix `_memcp_`, and a key in the reserved set
  (`backends/base.py`), the same on every backend.
  `update_memory`'s `content` is optional. An update must give `content`, a non-empty
  `metadata`, or both; otherwise it gets `validation_error`. `_memcp_` keys are removed
  from output.
- **Limits:** page sizes, result counts, import sizes, and metadata key count, key length
  and value size outside their ranges are refused.
- **Errors:** a backend error reaches the client as a fixed code and message; the store's
  response goes to the server log only.
- **Audit:** every policy decision is logged to a structured audit logger: operation, key
  name (never a token), space, memory ids or scope and count, relation, outcome. Never
  content.
- **Capabilities:** a tool exists only when the backend declares the operation behind it;
  export and import need `list`, and import with `on_conflict=overwrite` also needs
  `update`.
  `memory_status` reports the backend's capabilities, retrieval kind and whether it
  extracts facts.

## Backend-specific tools (planned)

A backend can offer MCP tools beyond the standard set when its store can do something they
can't express, such as a graph backend answering "what's connected to project X?". The
backend declares each tool's name, argument schema, the relation it needs, and a handler.
`tools/` registers it under a name prefixed with the backend (`mem0_…`) and refuses a name
that collides with another tool. Each call goes through `policy/`, which checks the
relation, calls the handler with the authorized partition, and applies the space check to
the memcp types the handler returns. These tools are versioned with their backend and kept
out of the frozen surface in `docs/tool-surface.json`.

Planned in [#107](https://github.com/Jartan-LLC/memcp/issues/107), with the other parts of
the contract a third-party backend needs: an `open_space` hook for stores that create their
own ids for a space, a trusted bulk-delete capability with a list-and-delete fallback, and
refusing a backend that hasn't passed the conformance suite.
