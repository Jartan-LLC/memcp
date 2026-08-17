# memcp

[![PyPI](https://img.shields.io/pypi/v/memcp-server)](https://pypi.org/project/memcp-server/)
[![CI](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

Backend-agnostic, multi-tenant MCP memory server. AI clients connect and get persistent long-term memory over streamable HTTP.

One command provisions the memory backend too — you do not stand up a memory engine first and then wire memcp to it.

**What you get with no account and no API key:** `memcp up` gives you durable,
multi-tenant memory. Retrieval on that default backend is keyword matching, not
semantic search, and nothing is extracted from what you store — the semantic and
graph engines need a key.

## Features

- One-command deployment that provisions memcp **and** its memory backend
- Durable, keyless local storage (`sqlite`), or mem0 on pgvector when you want semantic search and a knowledge graph
- Add, search, list, update and delete memories over MCP
- Flat scope-based filtering (agent_id, run_id)
- Per-tenant bearer token auth, minted per deployment, never defaulted
- Stateless HTTP transport — safe behind reverse proxies

## Getting Started

### 1. One command

```bash
pipx install memcp-server
memcp up
```

Needs Docker and nothing else. It creates the stack, waits until it is healthy,
prints a bearer token once, and prints the MCP client snippet containing it. The
backend `memcp up` provisions by default is `sqlite` — durable, no account, no key,
keyword retrieval. `memory_status` reports which backend you are on and whether it
extracts facts, so a client can find out without reading this file.

**Time to first memory: under 20 seconds.** That is the wall clock from `memcp up`
starting to `add_memory` then `search_memory` both succeeding over MCP, on a clean
GitHub-hosted runner with an empty image cache: 18.4s and 19.2s across two runs,
nearly all of it building and starting the container — the round trip itself is
0.11s. The `provision` job measures it on every pull request and publishes
`TIME_TO_FIRST_MEMORY_SECONDS` to its summary, so these are numbers this repository
ran rather than ones it estimated.

`memcp up --backend mem0` is the slower path — 54s to healthy and 2.4s for the first
memory — because it builds mem0 from source and starts pgvector beside it.

Reproduce either on your own machine with:

```bash
memcp plan          # everything it will create, before it creates any of it
memcp up --smoke    # create it, then store and retrieve one memory over MCP
```

`memcp down` stops it and keeps the memories. `docs/deployment.md` covers backends,
the mem0 stack, credential handling and what each command does.

### Running the server without provisioning

```bash
pip install memcp-server
MEMCP_BACKEND=sqlite MEMCP_HOST=127.0.0.1 python -m memcp
```

Or from source. This project installs with [uv](https://docs.astral.sh/uv/getting-started/installation/)
rather than pip — in CI, in the devcontainer and in the Docker image:

```bash
git clone https://github.com/Jartan-LLC/memcp.git
cd memcp
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
MEMCP_BACKEND=sqlite MEMCP_HOST=127.0.0.1 python -m memcp
```

The server starts on `http://localhost:8080`. With no `MEMCP_AUTH_TOKENS` set it
serves every request as one tenant, so it refuses to start on any interface another
machine can reach — set a token, or keep it on loopback as above.

### 2. Connect from Claude Code

Add to your MCP settings (Claude Code → Settings → MCP Servers):

```json
{
  "mcpServers": {
    "memcp": {
      "type": "streamable-http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

For authenticated deployments, add the `headers` field:

```json
{
  "mcpServers": {
    "memcp": {
      "type": "streamable-http",
      "url": "https://your-host:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

### 3. Try it

Ask Claude to remember something:
> "Remember that I prefer Python 3.12 and use ruff for linting."

In a new conversation, ask:
> "What linter do I use?"

Claude searches memory automatically and uses the stored context. On the default
`sqlite` backend this works because `linter` and `linting` share a stem — matching is
lexical, so a question phrased in words that do not appear in the memory will not
find it. `mem0` is the backend that matches on meaning.

## Configuration

### Requirements

- Docker, for `memcp up`
- Python 3.12+, to run the server directly
- For `MEMCP_BACKEND=mem0`: a running [mem0](https://github.com/mem0ai/mem0) instance — or let `memcp up --backend mem0` provision one

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MEMCP_BACKEND` | No | Backend for the server itself: `mem0`, `sqlite` or `in_memory`. Defaults to `mem0` when you run the server directly; `memcp up` provisions `sqlite` unless you pass `--backend` |
| `MEMCP_SQLITE_PATH` | No | Database file for the sqlite backend (default: `memcp.sqlite3`) |
| `MEM0_API_BASE` | mem0 | Base URL of your mem0 REST API |
| `MEM0_API_KEY` | mem0 | API key for the mem0 server |
| `MEMCP_AUTH_TOKENS` | No | Token-to-user mapping: `tok1:alice,tok2:bob`. Unset means unauthenticated, which is refused on any non-loopback bind |
| `MEMCP_HOST` | No | Bind address (default: `0.0.0.0`) |
| `MEMCP_PORT` | No | Bind port (default: `8080`) |
| `MEMCP_ALLOWED_HOSTS` | No | Host header allow-list for DNS-rebinding protection, comma-separated, `:*` for any port. Unset leaves the MCP SDK's rule, which is off unless `MEMCP_HOST` is loopback |
| `MEMCP_LOG_LEVEL` | No | Log level (default: `INFO`) |
| `MEMCP_LOG_FORMAT` | No | Log format: `json` or `plain` (default: `json`) |

## MCP Tools

### Universal (always available)

| Tool | Description |
|---|---|
| `add_memory` | Store a fact/preference/decision. On `mem0`, extracts facts by default (may store nothing) and `infer=false` stores verbatim. On `sqlite` and `in_memory` there is no model, so content is always stored verbatim and `infer` has no effect — the tool's own description says which, per deployment. Bulk: use `import_memories` |
| `search_memory` | Ranked search — semantic on `mem0`, keyword on `sqlite` and `in_memory`. `threshold` filters by minimum score (0-1). For browsing: `list_memories` |
| `delete_memory` | Delete one memory by ID. Confirm with user first |
| `delete_all_memories` | Bulk-delete by scope (e.g. agent_id, run_id), not content. Requires at least one scope key. Confirm first |
| `memory_status` | Returns server version, backend type, capabilities, valid scope keys, whether the backend extracts facts, and whether retrieval is semantic or keyword. No memory content |

### Optional (backend-dependent)

| Tool | Description |
|---|---|
| `get_memory` | Fetch one memory by ID. Returns full content, scope, and metadata |
| `update_memory` | Full-replace a memory's content (not a patch). Scope immutable — to change scope, add new + delete old |
| `list_memories` | Browse memories, optionally filtered by scope. Unranked, paginated. For semantic queries: `search_memory` |
| `export_memories` | Export memories as JSON (max 10k, truncates with flag). For backup/migration. Output compatible with `import_memories` (requires `list_memories`) |
| `import_memories` | Batch-import from JSON. Dedup via exact content match (scope-independent). `on_conflict`: skip, overwrite, duplicate (requires `list_memories`; overwrite requires `update_memory`) |
| `memory_history` | Change log for a memory: timestamps and previous/current content per create/update event |
| `memory_entities` | Knowledge graph: entities and relationships. Registered only by a backend that has one — `mem0` does, `sqlite` and `in_memory` do not, so it is absent on a keyless install. Not a search tool — use `search_memory` for topics |

## Docker

`memcp up` is the supported path — it provisions the backend too. For a
hand-managed single-service stack:

```bash
cp .env.example .env   # set MEMCP_AUTH_TOKENS, pick a backend
docker compose up -d
```

## Development

```bash
ruff check memcp/ tests/
ruff format --check memcp/ tests/
pyright
python -c "import memcp"
pytest -x
```

### Backend conformance

Any `MemoryBackend` implementation, in this repository or not, is held to one suite:

```bash
python -m memcp.conformance
```

It prints, per backend, every capability it implements and every one it does not. A
backend that declares a capability and fails its tests fails the run; only an
undeclared capability skips. `docs/conformance.md` covers backend selection,
out-of-tree adapters, and what the suite deliberately does not check.

Switching backends is held to the same bar. The suite migrates a 24-memory corpus
across three scopes between every pair of backends and asserts that content, scope
and retrieval by the original query all survive. What does not survive is written
down per pair in `docs/portability.md` and asserted against — an undocumented loss
fails CI rather than passing quietly.

Both run on every pull request against a real mem0, stood up locally with no API key
(`ci/mem0/up.sh`).

## Known Limitations

**mem0 backend (upstream constraints):**
- Nested boolean filters (AND/OR/NOT) return 502 — use flat scope keys
- List endpoint does not paginate server-side — full dataset loaded per request
- List returns at most 1000 memories per call (mem0's own ceiling), so an export of a
  tenant above that is incomplete and does not say so
- List endpoint does not filter by metadata
- Entities endpoint does not filter by user — post-filtered client-side
- Single-ID endpoints are globally scoped — ownership verified via fetch-then-verify

**sqlite and in-memory backends (no model behind them):**
- Retrieval is keyword matching, not vector similarity. Tokens match when they are equal or share a four-character prefix, so `linter` finds `linting` but a question phrased in different words finds nothing
- `add_memory` stores content verbatim. There is no fact extraction, so `infer` is accepted and ignored; `memory_status` reports `extracts_facts: false`
- No knowledge graph, so `memory_entities` is not registered at all

**In-memory backend, additionally:**
- Loses all data on restart

**General:**
- No date/time-based filtering on search or list
- No rate limiting (configure at reverse proxy layer)
- `delete_all_memories` deletes by scope structure, not content match

## Status

v0.2.0 — API may change before v1.0. Suitable for development and early adoption.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
