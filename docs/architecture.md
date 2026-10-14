# Architecture

How memcp is divided, what each part owns, and the rules a change is checked against.
For running memcp, see [deployment.md](deployment.md); for settings and tools,
[reference.md](reference.md).

## Packages

memcp is four packages over a shared set of data types. Calls go down the table, never up.

| Package | Owns | Never |
|---|---|---|
| `memcp/http/` | Receiving requests: HTTP, Host checks, `/health`, turning a bearer token into the caller's identity and grants | Touches memories |
| `memcp/tools/` | The MCP tools a client sees: names, argument schemas, result shapes, error responses | Decides a policy question, or calls a backend |
| `memcp/policy/` | Every rule memcp applies to an operation (below); import, export and migration; the only caller of a backend | Knows a store's API |
| `memcp/backends/` | One adapter per memory store (mem0, sqlite, in_memory): storing, searching, fetching, history | Decides a policy question |

`memcp/types.py` holds the data shapes every package shares (`Memory`, `HistoryEntry`, the
result types, `MemoryAPIError`) and imports nothing else from memcp. `memcp/config.py`
reads settings from the environment.

```text
memcp/
  __main__.py, config.py, types.py
  http/       app.py (create_app, lifespan, /health), auth.py, hosts.py, logging.py
  tools/      __init__.py (register_tools), memories.py, bulk.py, transfer.py, insight.py, surface.py
  policy/     __init__.py (one method per operation), access.py, scope.py, metadata.py,
              results.py, transfer.py, audit.py
  backends/   base.py (the adapter contract), capabilities.py, keyword.py, in_memory.py,
              sqlite.py, mem0/ (client.py, adapter.py)
```

Tests mirror the packages (`tests/http/`, `tests/tools/`, `tests/policy/`,
`tests/backends/`); `tests/conformance/` holds every backend to the adapter contract.

## Rules for changes

A policy rule is a decision about what memcp accepts from a client or returns to it: which
space a call reaches, which scope values are allowed, which metadata keys a client can't
set, who the author is, how many results come back.

1. Each policy rule is written once, in `memcp/policy/`. The same check in `tools/` or a
   backend breaks this rule.
2. Imports go down only: `http/` → `tools/` → `policy/` → `backends/base.py` → each
   backend. A backend imports only `types.py` and `backends/base.py`, and only `policy/`
   calls a backend method. A test fails on an upward import.
3. Adding a backend means writing one class that implements `backends/base.py` and declares
   its optional capabilities. Needing an edit to `tools/` or `policy/` beyond registering
   it means the contract is missing something.
4. Policy validates every argument before calling a backend, and checks every result on the
   way back (space, scope, count).
5. Each policy rule has a test that calls `policy/` directly with input that breaks it.
   What a backend promises is tested by `tests/conformance` against every backend.

## Tenancy

memcp enforces tenancy itself, whatever the store supports. Where a store has its own
isolation (mem0's `user_id`), it is used as well, so both apply.

| Concept | What it is |
|---|---|
| Space | Where memories live, and the unit of isolation. Every memory belongs to exactly one space, for life. |
| Partition | The opaque string a backend stores for a space. Backends never see anything else about tenancy. |
| Key | Who is calling: one bearer token, or several sharing a seat. |
| Grant | Key X may use space Y with a relation: `reader`, `writer` or `manager`. `manager` doesn't imply reading. |

Today each token in `MEMCP_AUTH_TOKENS` names one space, which is its home space: the
tenant string is the partition, so stored data never changes. Each key has one grant on
its home space, `writer`, or `reader` when configured read-only. A token without a seat in
`MEMCP_AUTH_SEATS` is unattributed.

Spaces registered by memcp itself, public `spc_` ids, a `space` argument on tools, reads
across spaces, users, organizations, shared spaces and an admin plane are planned in
[#112](https://github.com/Jartan-LLC/memcp/issues/112). Each adds data or optional
methods; none changes the partition of a stored memory or the meaning of an existing call.

### How policy enforces it

- **The space comes from the grant,** never from arguments, metadata or an import payload.
  Every backend call names exactly one partition.
- **Every write is stamped.** Policy sets `system["space"]`, `system["author"]` and the
  write-once `system["created_by"]` on the memory; callers can't set `system`.
- **Every result is checked.** Each returned memory's stored partition and space stamp
  must equal the partition policy asked for. On a mismatch the request fails, an
  `isolation_violation` error is logged without content, and nothing is returned. A memory
  written before stamps existed is checked on its partition alone.
- **Calls by id check ownership first.** Before update, delete or history, policy fetches
  the memory and checks its space; a memory in another space and a missing one return the
  same `not_found`.
- **Bulk delete needs a scope.** An empty scope, or an empty or wildcard value, is refused.
- **Startup checks every partition and seat:** non-empty, at most 128 characters, no
  whitespace, comma or `*`, no reserved prefix (`key_`, `usr_`, `org_`, `acct_`, `spc_`,
  `__`). A seat mapped to two different tenants is refused.
- **Every decision is logged** to a structured audit logger: operation, key, space, memory
  ids or scope and count, relation, outcome. Never content.

## What a backend guarantees

`backends/base.py` is the contract. A backend:

- takes `partition: str` on every data method, stores it byte for byte, and filters every
  search, list and delete by exact equality on it, ANDed with the scope;
- stores the `system` mapping verbatim and returns it, with the stored partition, on every
  memory;
- returns `None` or raises a 404 from `get`, `update`, `delete` and `history` when the id is
  missing or belongs to another partition, the same either way;
- never changes a memory's partition;
- rejects a partition its store can't match by exact equality (`validate_partition`);
- refuses `delete_all` with an empty scope;
- declares its optional capabilities honestly and raises `NotImplementedError` for the rest;
- maps store errors to `MemoryAPIError`.

`tests/conformance` checks each of these against every backend, including two-space
isolation for every operation and a returned memory carrying a foreign stamp.

## Policy beyond tenancy

- **Scope:** one function validates every scope before any backend call: allowed keys,
  value types and lengths, key count, `agent_id` normalization, no `user_id`, no nested
  filters, no wildcard, empty or null values. `agent_id` and `run_id` organize memories
  inside a space; they are not a security boundary.
- **Metadata:** keys under the reserved prefix are removed from input and output.
- **Limits:** page sizes, result counts and import sizes are clamped on the way in and
  checked on the way out.
- **Capabilities:** a tool exists only when the backend declares the capability behind it.

## Backend-specific tools

Not built yet; [#107](https://github.com/Jartan-LLC/memcp/issues/107) builds it.

A backend can offer MCP tools beyond the standard set when its store can do something they
can't express, such as a graph backend answering "what's connected to project X?". The
backend declares each tool's name, argument schema and handler. `tools/` registers it under
a name prefixed with the backend (`mem0_…`), and every call runs through `policy/`, so the
space, grants, limits and audit apply as for a standard tool. These tools are versioned
with their backend and kept out of the frozen surface in `tool-surface.json`.
